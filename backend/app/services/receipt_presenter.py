import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ROLE_ADMIN, RequestContext
from app.models.receipt import Receipt
from app.schemas.receipt import ReceiptResponse

# Flag-details keys that point at another receipt: the image-duplicate check
# and the historical content-match anomaly.
_KEYS = ("duplicate_receipt_id", "matching_receipt_id")


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def present_receipts(
    receipts: list[Receipt], context: RequestContext, db: AsyncSession
) -> list[ReceiptResponse]:
    """Build response models for receipts.

    Members must not learn about colleagues' receipts through duplicate flags,
    so receipt references (see `_KEYS`) are dropped from flag details, and the
    "receipt #N" in the flag text is generalised, unless the reference points
    at one of the member's own receipts. Redaction only touches the response
    copies; ORM objects (and therefore the database) are never modified.
    """
    responses = [ReceiptResponse.model_validate(r) for r in receipts]
    if context.role == ROLE_ADMIN:
        return responses

    ids = {
        i
        for resp in responses
        for flag in resp.fraud_flags
        for key in _KEYS
        if flag.details and key in flag.details
        and (i := _as_int(flag.details[key])) is not None
    }
    own: set[int] = set()
    if ids:
        rows = await db.execute(
            select(Receipt.id).where(
                Receipt.id.in_(ids),
                Receipt.tenant_id == context.tenant_id,
                Receipt.submitted_by_user_id == context.user_id,
            )
        )
        own = set(rows.scalars().all())

    for resp in responses:
        for flag in resp.fraud_flags:
            for key in _KEYS:
                if not flag.details or key not in flag.details:
                    continue
                target = _as_int(flag.details[key])
                if target in own:
                    continue
                flag.details = {k: v for k, v in flag.details.items() if k != key}
                if flag.description and target is not None:
                    flag.description = re.sub(
                        rf"receipt #{target}\b",
                        "another receipt in your business",
                        flag.description,
                    )
    return responses
