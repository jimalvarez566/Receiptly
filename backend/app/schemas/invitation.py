from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class InvitationCreate(BaseModel):
    role: Literal["admin", "member"] = "member"


class InvitationCreated(BaseModel):
    id: int
    role: str
    expires_at: datetime
    token: str


class InvitationOut(BaseModel):
    id: int
    role: str
    status: Literal["pending", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime
    created_by_user_id: UUID
    accepted_by_user_id: UUID | None = None


class InvitationPreview(BaseModel):
    tenant_name: str
    role: str
    expires_at: datetime


class InvitationAccepted(BaseModel):
    tenant_id: int
    tenant_name: str
    role: str
    already_member: bool
