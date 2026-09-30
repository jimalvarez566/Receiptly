from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ReceiptBase(BaseModel):
    merchant: str | None = None
    amount: Decimal | None = None
    transaction_date: date | None = None
    category: str | None = None
    employee_id: int | None = None


class ReceiptCreate(ReceiptBase):
    """Fields that can be provided when uploading a receipt (besides the file)."""
    pass


class FraudFlagResponse(BaseModel):
    """Fraud flag as returned in API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    flag_type: str
    severity: str
    description: str | None
    details: dict | None
    confidence_score: Decimal | None
    created_at: datetime


class ReceiptResponse(BaseModel):
    """Receipt as returned in API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    submitted_by_user_id: UUID
    employee_id: int | None
    image_path: str
    image_hash: str | None

    merchant: str | None
    amount: Decimal | None
    transaction_date: date | None
    category: str | None
    items: dict | None

    ocr_confidence: Decimal | None
    ocr_method: str | None

    fraud_score: int | None
    risk_level: str | None
    status: str
    explanation: str | None

    created_at: datetime
    analyzed_at: datetime | None
    reviewed_at: datetime | None

    fraud_flags: list[FraudFlagResponse] = []


class ReceiptListResponse(BaseModel):
    """Paginated list of receipts."""
    receipts: list[ReceiptResponse]
    total: int
    page: int
    per_page: int


class ReviewRequest(BaseModel):
    """Body for the review endpoint."""
    decision: Literal["approved", "rejected"]
    note: str | None = None


class ReviewResponse(ReceiptResponse):
    """Review response — same as ReceiptResponse plus duplicate chain context.

    original_receipt_id is set when the reviewed receipt has duplicate flags,
    identifying the earliest submission with the same image hash. Helps
    reviewers distinguish the original from a re-submission.
    """
    original_receipt_id: int | None = None
