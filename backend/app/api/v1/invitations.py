from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.tenants import _require_membership
from app.auth import RequestContext, require_admin
from app.config import settings
from app.database import get_db
from app.models import Invitation
from app.schemas.invitation import InvitationCreate, InvitationCreated, InvitationOut
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
