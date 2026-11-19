from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_user_id: Mapped[Uuid] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)
    accepted_by_user_id: Mapped[Uuid | None] = mapped_column(Uuid, default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint("role IN ('admin','member')", name="ck_invitations_role"),
        Index("idx_invitations_tenant", "tenant_id"),
    )
