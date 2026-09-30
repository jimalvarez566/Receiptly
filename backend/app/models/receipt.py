from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    submitted_by_user_id: Mapped[Uuid] = mapped_column(Uuid)

    # File storage
    image_path: Mapped[str] = mapped_column(Text)
    image_hash: Mapped[str | None] = mapped_column(String(64))

    # Extracted data (from OCR)
    merchant: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    transaction_date: Mapped[date | None]
    category: Mapped[str | None] = mapped_column(String(100))
    items: Mapped[dict | None] = mapped_column(JSONB)

    # Metadata
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ocr_method: Mapped[str | None] = mapped_column(String(20))

    # Analysis results
    fraud_score: Mapped[int | None]
    risk_level: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(50), default="pending")

    # AI explanation (cached Gemini output)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    analyzed_at: Mapped[datetime | None]
    reviewed_at: Mapped[datetime | None]

    # Relationships
    employee: Mapped["Employee | None"] = relationship(back_populates="receipts")
    fraud_flags: Mapped[list["FraudFlag"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_receipts_employee", "employee_id"),
        Index("idx_receipts_image_hash", "image_hash"),
        Index("idx_receipts_status", "status"),
        Index("idx_receipts_tenant", "tenant_id"),
    )
