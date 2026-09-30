from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Membership, Tenant
from app.schemas.tenant import MeResponse, MembershipInfo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    result = await db.execute(
        select(Tenant.id, Tenant.name, Membership.role)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Tenant.name)
    )
    memberships = [
        MembershipInfo(tenant_id=row.id, name=row.name, role=row.role)
        for row in result.all()
    ]
    return MeResponse(user_id=user.user_id, email=user.email, memberships=memberships)
