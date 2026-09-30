from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import supabase_client
from app.auth import CurrentUser, RequestContext, get_current_context, get_current_user
from app.database import get_db
from app.models import Membership, Tenant
from app.schemas.tenant import (
    MemberAddRequest,
    MemberResponse,
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
    db.add(Membership(tenant_id=tenant.id, user_id=user.user_id, role="owner"))
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
    context: RequestContext = Depends(get_current_context),
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
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_membership(tenant_id, context)
    try:
        parsed_user_id = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Membership not found")
    row = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == parsed_user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.flush()
