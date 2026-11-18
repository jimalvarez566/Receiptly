from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import supabase_client
from app.auth import (
    CurrentUser,
    RequestContext,
    get_current_context,
    get_current_user,
    require_admin,
)
from app.database import get_db
from app.models import Membership, Tenant
from app.schemas.tenant import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdate,
    TenantCreate,
    TenantResponse,
)
from app.services.policy_defaults import build_default_rules

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    tenant = Tenant(name=body.name, created_by_user_id=user.user_id)
    db.add(tenant)
    await db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.user_id, role="admin"))
    db.add_all(build_default_rules(tenant.id))
    await db.flush()
    return tenant


@router.get("", response_model=list[TenantResponse])
async def list_my_tenants(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Tenant]:
    result = await db.execute(
        select(Tenant)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Tenant.name)
    )
    return list(result.scalars().all())


async def _require_membership(tenant_id: int, context: RequestContext) -> None:
    if context.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="X-Tenant-ID does not match the tenant in the path",
        )


LAST_ADMIN_MSG = "A business needs at least one admin"


def _parse_user_id(user_id: str) -> UUID:
    try:
        return UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Membership not found")


async def _lock_admin_ids(
    db: AsyncSession, tenant_id: int, context: RequestContext
) -> set[UUID]:
    """Lock the tenant's admin membership rows (FOR UPDATE) and return their
    user ids. The caller's admin role is re-verified against the locked rows,
    since it may have been revoked after authentication but before the lock."""
    result = await db.execute(
        select(Membership.user_id)
        .where(Membership.tenant_id == tenant_id, Membership.role == "admin")
        .with_for_update()
    )
    ids = {row[0] for row in result.all()}
    if context.user_id not in ids:
        raise HTTPException(status_code=403, detail="Only admins can manage members")
    return ids


async def _get_membership(
    db: AsyncSession, tenant_id: int, user_id: UUID
) -> Membership | None:
    return (
        await db.execute(
            select(Membership)
            .where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


@router.get("/{tenant_id}/members", response_model=list[MemberResponse])
async def list_members(
    tenant_id: int,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> list[Membership]:
    await _require_membership(tenant_id, context)
    result = await db.execute(
        select(Membership).where(Membership.tenant_id == tenant_id)
    )
    return list(result.scalars().all())


@router.post("/{tenant_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    tenant_id: int,
    body: MemberAddRequest,
    response: Response,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> Membership:
    await _require_membership(tenant_id, context)
    user_id = await supabase_client.lookup_user_id_by_email(body.email)
    if user_id is None:
        raise HTTPException(
            status_code=404,
            detail="No account for that email; ask them to sign up first",
        )
    existing = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing
    membership = Membership(tenant_id=tenant_id, user_id=user_id, role="member")
    db.add(membership)
    await db.flush()
    return membership


@router.delete("/{tenant_id}/members/{user_id}", status_code=204)
async def remove_member(
    tenant_id: int,
    user_id: str,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_membership(tenant_id, context)
    parsed_user_id = _parse_user_id(user_id)
    # Lock the admin rows before reading the target so concurrent removals or
    # demotions are serialised and the count below is accurate.
    admin_ids = await _lock_admin_ids(db, tenant_id, context)
    row = await _get_membership(db, tenant_id, parsed_user_id)
    if row is None:
        return
    if row.role == "admin" and len(admin_ids) <= 1:
        raise HTTPException(status_code=400, detail=LAST_ADMIN_MSG)
    await db.delete(row)
    await db.flush()


@router.patch("/{tenant_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    tenant_id: int,
    user_id: str,
    body: MemberRoleUpdate,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> Membership:
    await _require_membership(tenant_id, context)
    parsed_user_id = _parse_user_id(user_id)
    admin_ids = await _lock_admin_ids(db, tenant_id, context)
    row = await _get_membership(db, tenant_id, parsed_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    if row.role == "admin" and body.role != "admin" and len(admin_ids) <= 1:
        raise HTTPException(status_code=400, detail=LAST_ADMIN_MSG)
    row.role = body.role
    await db.flush()
    return row
