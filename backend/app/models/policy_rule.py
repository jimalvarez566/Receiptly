from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PolicyRule(Base):
    __tablename__ = "policy_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    rule_name: Mapped[str] = mapped_column(String(100))
    rule_type: Mapped[str] = mapped_column(String(50))

    parameters: Mapped[dict] = mapped_column(JSONB)

    is_active: Mapped[bool] = mapped_column(default=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (Index("idx_policy_rules_tenant", "tenant_id"),)
