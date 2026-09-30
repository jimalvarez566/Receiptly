import logging
from pathlib import Path

import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fraud_flag import FraudFlag
from app.models.receipt import Receipt

logger = logging.getLogger(__name__)

# Hamming distance at or below this value is considered a duplicate.
# pHash produces a 64-bit hash; identical images score 0, minor edits score 1-5,
# different images typically score 20+. 10 is a conservative safe threshold.
_DUPLICATE_THRESHOLD = 10


def compute_image_hash(file_path: str) -> str | None:
    """Compute a perceptual hash (pHash) for a receipt image or PDF first page.

    Returns the hash as a hex string (e.g. "f8e0c0a0..."), or None on failure.
    pHash is robust to minor resizing, compression artifacts, and colour shifts,
    making it suitable for detecting re-submitted or slightly-edited receipt images.
    """
    path = Path(file_path)
    try:
        if path.suffix.lower() == ".pdf":
            try:
                from pdf2image import convert_from_path
                pages = convert_from_path(str(path), last_page=1)
                if not pages:
                    return None
                img = pages[0]
            except ImportError:
                logger.warning("pdf2image not available; cannot hash PDF %s", path.name)
                return None
        else:
            img = Image.open(path)

        return str(imagehash.phash(img))

    except Exception:
        logger.exception("Failed to compute pHash for %s", file_path)
        return None


async def detect_duplicates(
    receipt: Receipt,
    db: AsyncSession,
) -> list[FraudFlag]:
    """Check whether any existing receipt is a near-duplicate of this one.

    Fetches all stored image hashes and compares using Hamming distance.
    Returns a list of FraudFlag ORM objects (not yet added to the session)
    for each duplicate found.
    """
    receipt_id = receipt.id
    image_hash = receipt.image_hash
    if image_hash is None:
        return []

    result = await db.execute(
        select(Receipt.id, Receipt.image_hash)
        .where(Receipt.tenant_id == receipt.tenant_id)
        .where(Receipt.image_hash.is_not(None))
        .where(Receipt.id != receipt_id)
    )
    rows = result.all()

    if not rows:
        return []

    current = imagehash.hex_to_hash(image_hash)
    flags: list[FraudFlag] = []

    for existing_id, stored_hash_str in rows:
        try:
            stored = imagehash.hex_to_hash(stored_hash_str)
            distance = current - stored
        except Exception:
            logger.warning("Could not compare hash for receipt %d", existing_id)
            continue

        if distance <= _DUPLICATE_THRESHOLD:
            # Cast to native Python types — NumPy scalars from imagehash are not
            # JSON-serializable and will cause a TypeError on JSONB insert.
            distance_int = int(distance)
            existing_id_int = int(existing_id)
            confidence = round(float(100 - (distance_int / 64 * 100)), 2)

            logger.warning(
                "Duplicate detected: receipt %d matches receipt %d (distance=%d)",
                receipt_id,
                existing_id_int,
                distance_int,
            )
            flags.append(FraudFlag(
                receipt_id=receipt_id,
                flag_type="duplicate",
                severity="high",
                description=(
                    f"Receipt image is nearly identical to receipt #{existing_id_int} "
                    f"(similarity distance: {distance_int}/64)."
                ),
                details={"duplicate_receipt_id": existing_id_int, "hash_distance": distance_int},
                confidence_score=confidence,
            ))

    return flags
