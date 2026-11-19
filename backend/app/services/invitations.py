import hashlib
import secrets
from datetime import datetime
from typing import Literal

from app.models import Invitation

InvitationStatus = Literal["pending", "used", "revoked", "expired"]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_status(inv: Invitation, now: datetime | None = None) -> InvitationStatus:
    now = now or datetime.utcnow()
    if inv.revoked_at is not None:
        return "revoked"
    if inv.accepted_at is not None:
        return "used"
    if inv.expires_at <= now:
        return "expired"
    return "pending"
