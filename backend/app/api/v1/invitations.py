from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tenants import _require_membership
from app.api.v1.receipts import limiter
from app.auth import CurrentUser, RequestContext, get_current_user, require_admin
from app.config import settings
from app.database import get_db
from app.models import Invitation, Membership, Tenant
from app.schemas.invitation import (
    InvitationAccepted,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPreview,
)
from app.services.invitations import hash_token, invitation_status, new_token

router = APIRouter(tags=["invitations"])

TenantId = Annotated[int, Path(ge=1, le=2**31 - 1)]
InvitationId = Annotated[int, Path(ge=1, le=2**31 - 1)]
LIST_LIMIT = 50


@router.post(
    "/tenants/{tenant_id}/invitations",
    response_model=InvitationCreated,
    status_code=201,
)
async def create_invitation(
    tenant_id: TenantId,
    body: InvitationCreate,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> InvitationCreated:
    await _require_membership(tenant_id, context)
    token = new_token()
    inv = Invitation(
        tenant_id=tenant_id,
        role=body.role,
        token_hash=hash_token(token),
        created_by_user_id=context.user_id,
        expires_at=datetime.utcnow() + timedelta(days=settings.INVITE_TTL_DAYS),
    )
    db.add(inv)
    await db.flush()
    return InvitationCreated(
        id=inv.id, role=inv.role, expires_at=inv.expires_at, token=token
    )


@router.get("/tenants/{tenant_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    tenant_id: TenantId,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> list[InvitationOut]:
    await _require_membership(tenant_id, context)
    rows = (
        await db.execute(
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .order_by(Invitation.created_at.desc(), Invitation.id.desc())
            .limit(LIST_LIMIT)
        )
    ).scalars().all()
    now = datetime.utcnow()
    return [
        InvitationOut(
            id=i.id,
            role=i.role,
            status=invitation_status(i, now),
            created_at=i.created_at,
            expires_at=i.expires_at,
            created_by_user_id=i.created_by_user_id,
            accepted_by_user_id=i.accepted_by_user_id,
        )
        for i in rows
    ]


@router.delete("/tenants/{tenant_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    tenant_id: TenantId,
    invitation_id: InvitationId,
    context: RequestContext = Depends(require_admin("manage members")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_membership(tenant_id, context)
    inv = (
        await db.execute(
            select(Invitation)
            .where(Invitation.id == invitation_id, Invitation.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if inv.revoked_at is not None:
        return
    if inv.accepted_at is not None:
        raise HTTPException(status_code=409, detail="That invitation was already used")
    inv.revoked_at = datetime.utcnow()
    await db.flush()


_NOT_VALID = "This invitation link isn't valid"
_UNUSABLE = {
    "revoked": "This invitation was revoked",
    "used": "This invitation has already been used",
    "expired": "This invitation has expired",
}
TokenPath = Annotated[str, Path(min_length=1, max_length=200)]


@router.get("/invitations/{token}", response_model=InvitationPreview)
@limiter.limit("60/minute")
async def preview_invitation(
    request: Request,
    token: TokenPath,
    db: AsyncSession = Depends(get_db),
) -> InvitationPreview:
    """Public: what a link would join. Reveals only business name, role, expiry."""
    row = (
        await db.execute(
            select(Invitation, Tenant.name)
            .join(Tenant, Tenant.id == Invitation.tenant_id)
            .where(Invitation.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_VALID)
    inv, tenant_name = row
    status = invitation_status(inv)
    if status != "pending":
        raise HTTPException(status_code=410, detail=_UNUSABLE[status])
    return InvitationPreview(tenant_name=tenant_name, role=inv.role, expires_at=inv.expires_at)


@router.post("/invitations/{token}/accept", response_model=InvitationAccepted)
async def accept_invitation(
    token: TokenPath,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InvitationAccepted:
    # Lock the invitation row so a concurrent revoke/accept serialises with us.
    inv = (
        await db.execute(
            select(Invitation)
            .where(Invitation.token_hash == hash_token(token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail=_NOT_VALID)
    tenant_name = (
        await db.execute(select(Tenant.name).where(Tenant.id == inv.tenant_id))
    ).scalar_one()

    async def _existing_role() -> str | None:
        return (
            await db.execute(
                select(Membership.role).where(
                    Membership.tenant_id == inv.tenant_id,
                    Membership.user_id == user.user_id,
                )
            )
        ).scalar_one_or_none()

    def _already(role: str) -> InvitationAccepted:
        return InvitationAccepted(
            tenant_id=inv.tenant_id, tenant_name=tenant_name, role=role, already_member=True
        )

    role = await _existing_role()
    if role is not None:
        return _already(role)

    status = invitation_status(inv)
    if status != "pending":
        raise HTTPException(status_code=410, detail=_UNUSABLE[status])

    try:
        async with db.begin_nested():
            db.add(Membership(tenant_id=inv.tenant_id, user_id=user.user_id, role=inv.role))
            await db.flush()
    except IntegrityError:
        # Same user joined this business concurrently via another invitation.
        return _already((await _existing_role()) or inv.role)

    inv.accepted_at = datetime.utcnow()
    inv.accepted_by_user_id = user.user_id
    await db.flush()
    return InvitationAccepted(
        tenant_id=inv.tenant_id, tenant_name=tenant_name, role=inv.role, already_member=False
    )
