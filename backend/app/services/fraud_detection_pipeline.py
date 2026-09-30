import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fraud_flag import FraudFlag
from app.models.receipt import Receipt
from app.services.duplicate_detector import detect_duplicates
from app.services.fraud_scorer import calculate_score
from app.services.policy_validator import validate_receipt

logger = logging.getLogger(__name__)


async def run_fraud_pipeline(receipt: Receipt, db: AsyncSession) -> list[FraudFlag]:
    """Run all fraud detectors on a receipt and update it in place.

    Executes in order:
    1. Duplicate detection  — uses receipt.image_hash (skipped if not set)
    2. Policy validation    — checks all active policy rules
    3. Fraud scoring        — combines flags into a 0-100 score + risk level

    Side effects (all reflected on the receipt object passed in):
    - Adds FraudFlag objects to the session (not yet committed)
    - Sets receipt.fraud_score and receipt.risk_level
    - Sets receipt.status to "flagged" if any flags, "analyzed" if clean

    Adding a Phase 2 detector (anomaly, AI) means adding one call here.

    Returns the list of generated flags (useful for caller logging).
    """
    all_flags: list[FraudFlag] = []

    # Step 1: duplicate detection (skipped when image_hash not yet computed)
    if receipt.image_hash:
        duplicate_flags = await detect_duplicates(receipt, db)
        if duplicate_flags:
            logger.warning(
                "Receipt %d — %d duplicate flag(s) detected",
                receipt.id, len(duplicate_flags),
            )
        all_flags.extend(duplicate_flags)

    # Step 2: policy validation
    policy_flags = await validate_receipt(receipt, db)
    if policy_flags:
        logger.warning(
            "Receipt %d — %d policy violation(s) detected",
            receipt.id, len(policy_flags),
        )
    all_flags.extend(policy_flags)

    # Step 3: score and persist
    fraud_score, risk_level = calculate_score(all_flags)
    receipt.fraud_score = fraud_score
    receipt.risk_level = risk_level

    if all_flags:
        for flag in all_flags:
            db.add(flag)
        receipt.status = "flagged"
    else:
        receipt.status = "analyzed"

    logger.info(
        "Pipeline complete for receipt %d — score=%d (%s), flags=%d",
        receipt.id, fraud_score, risk_level, len(all_flags),
    )

    return all_flags
