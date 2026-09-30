import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fraud_flag import FraudFlag
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt

logger = logging.getLogger(__name__)


async def validate_receipt(receipt: Receipt, db: AsyncSession) -> list[FraudFlag]:
    """Check a receipt against all active policy rules.

    Supported rule types:
    - amount_limit:           total exceeds a per-category cap
    - future_date:            transaction date is in the future
    - vendor_category:        merchant doesn't match any allowed expense category
    - round_number:           total is a suspiciously round number
    - short_window_duplicate: same employee submits a similar receipt within a time window

    Returns a list of FraudFlag ORM objects (not yet added to the session).
    """
    result = await db.execute(
        select(PolicyRule)
        .where(PolicyRule.is_active == True)  # noqa: E712
        .where(PolicyRule.tenant_id == receipt.tenant_id)
    )
    rules = result.scalars().all()

    if not rules:
        logger.debug("No active policy rules found; skipping validation")
        return []

    flags: list[FraudFlag] = []

    for rule in rules:
        try:
            # short_window_duplicate requires a DB query — handle async separately
            if rule.rule_type == "short_window_duplicate":
                flag = await _check_short_window_duplicate(rule, receipt, db)
            else:
                flag = _evaluate_rule(rule, receipt)
        except Exception:
            logger.exception("Error evaluating rule '%s' (id=%d)", rule.rule_name, rule.id)
            continue

        if flag:
            flags.append(flag)

    return flags


def _evaluate_rule(rule: PolicyRule, receipt: Receipt) -> FraudFlag | None:
    """Dispatch sync rule evaluation. Returns a FraudFlag or None."""
    if rule.rule_type == "amount_limit":
        return _check_amount_limit(rule, receipt)
    if rule.rule_type == "future_date":
        return _check_future_date(rule, receipt)
    if rule.rule_type == "vendor_category":
        return _check_vendor_category(rule, receipt)
    if rule.rule_type == "round_number":
        return _check_round_number(rule, receipt)

    logger.warning("Unknown rule type '%s' for rule '%s'; skipping", rule.rule_type, rule.rule_name)
    return None


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _check_amount_limit(rule: PolicyRule, receipt: Receipt) -> FraudFlag | None:
    """Flag when the receipt total exceeds the configured limit.

    If a category is set in the rule parameters, the check only applies when
    the receipt's category matches. Omitting category applies the limit to all
    receipts regardless of category.
    """
    if receipt.amount is None:
        return None

    params = rule.parameters
    limit = params.get("limit")
    if limit is None:
        logger.warning("Rule '%s' missing 'limit' parameter; skipping", rule.rule_name)
        return None

    rule_category = params.get("category")
    if rule_category and (receipt.category or "").lower() != rule_category.lower():
        return None

    limit_decimal = Decimal(str(limit))
    if receipt.amount <= limit_decimal:
        return None

    category_label = f"{rule_category} " if rule_category else ""
    return FraudFlag(
        receipt_id=receipt.id,
        flag_type="policy_violation",
        severity=rule.severity,
        description=(
            f"Receipt total ${receipt.amount} exceeds the {category_label}"
            f"limit of ${limit_decimal} (rule: '{rule.rule_name}')."
        ),
        details={
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "limit": float(limit_decimal),
            "actual_amount": float(receipt.amount),
            "category": rule_category,
        },
        confidence_score=Decimal("100.00"),
    )


def _check_future_date(rule: PolicyRule, receipt: Receipt) -> FraudFlag | None:
    """Flag when the transaction date is in the future."""
    if receipt.transaction_date is None:
        return None

    today = date.today()
    if receipt.transaction_date <= today:
        return None

    return FraudFlag(
        receipt_id=receipt.id,
        flag_type="policy_violation",
        severity=rule.severity,
        description=(
            f"Transaction date {receipt.transaction_date} is in the future "
            f"(rule: '{rule.rule_name}')."
        ),
        details={
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "transaction_date": str(receipt.transaction_date),
            "today": str(today),
        },
        confidence_score=Decimal("100.00"),
    )


def _check_vendor_category(rule: PolicyRule, receipt: Receipt) -> FraudFlag | None:
    """Flag when the merchant doesn't match any allowed expense category.

    Parameters store a dict of category -> keyword list. The merchant name is
    checked case-insensitively against every keyword in every allowed category.
    If at least one keyword matches, the receipt passes.

    Example parameters:
        {
            "allowed_categories": {
                "meals":    ["restaurant", "cafe", "starbucks", ...],
                "travel":   ["hotel", "delta", "marriott", ...],
                "supplies": ["staples", "amazon", "office depot", ...]
            }
        }
    """
    if receipt.merchant is None:
        return None

    allowed_categories: dict = rule.parameters.get("allowed_categories", {})
    if not allowed_categories:
        logger.warning("Rule '%s' has empty 'allowed_categories'; skipping", rule.rule_name)
        return None

    merchant_lower = receipt.merchant.lower()
    for category, keywords in allowed_categories.items():
        for keyword in keywords:
            if keyword.lower() in merchant_lower:
                logger.debug(
                    "Merchant '%s' matched category '%s' via keyword '%s'",
                    receipt.merchant, category, keyword,
                )
                return None  # matched — receipt is fine

    matched_categories = list(allowed_categories.keys())
    return FraudFlag(
        receipt_id=receipt.id,
        flag_type="policy_violation",
        severity=rule.severity,
        description=(
            f"Merchant '{receipt.merchant}' does not match any approved expense "
            f"category ({', '.join(matched_categories)}) "
            f"(rule: '{rule.rule_name}')."
        ),
        details={
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "merchant": receipt.merchant,
            "allowed_categories": matched_categories,
        },
        confidence_score=Decimal("85.00"),
    )


def _check_round_number(rule: PolicyRule, receipt: Receipt) -> FraudFlag | None:
    """Flag when the total is a suspiciously round number.

    Real transactions almost never land on clean numbers. We flag amounts that
    end in exactly .00 or .50 and exceed the configured minimum threshold (to
    avoid flagging a $5.00 coffee).

    Parameters: {"min_amount": 10.00}
    """
    if receipt.amount is None:
        return None

    min_amount = Decimal(str(rule.parameters.get("min_amount", 10.00)))
    if receipt.amount < min_amount:
        return None

    cents = receipt.amount % 1  # fractional part
    if cents not in (Decimal("0.00"), Decimal("0.50")):
        return None

    return FraudFlag(
        receipt_id=receipt.id,
        flag_type="policy_violation",
        severity=rule.severity,
        description=(
            f"Receipt total ${receipt.amount} is a suspiciously round number "
            f"(rule: '{rule.rule_name}')."
        ),
        details={
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "actual_amount": float(receipt.amount),
            "min_amount_threshold": float(min_amount),
        },
        confidence_score=Decimal("70.00"),
    )


async def _check_short_window_duplicate(
    rule: PolicyRule,
    receipt: Receipt,
    db: AsyncSession,
) -> FraudFlag | None:
    """Flag when the same employee submits a similar receipt within a time window.

    Catches re-photographed duplicates that slip past perceptual hashing
    (e.g., same receipt photographed twice at different angles or brightness).

    Parameters: {"window_hours": 48, "amount_tolerance_pct": 10}

    Matching criteria (all must hold):
    - Same employee_id
    - Merchant name overlap (one contains the other, case-insensitive)
    - Amount within tolerance percentage
    - Submitted (created_at) within the configured window
    """
    if receipt.employee_id is None or receipt.merchant is None or receipt.amount is None:
        return None

    window_hours: int = int(rule.parameters.get("window_hours", 48))
    tolerance_pct: float = float(rule.parameters.get("amount_tolerance_pct", 10))

    # Receipt.created_at is stored as a naive UTC timestamp; keep the cutoff
    # naive so the comparison is valid for the (TIMESTAMP WITHOUT TIME ZONE) column.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)

    result = await db.execute(
        select(Receipt.id, Receipt.merchant, Receipt.amount, Receipt.created_at)
        .where(Receipt.tenant_id == receipt.tenant_id)
        .where(Receipt.employee_id == receipt.employee_id)
        .where(Receipt.merchant.is_not(None))
        .where(Receipt.amount.is_not(None))
        .where(Receipt.id != receipt.id)
        .where(Receipt.created_at >= cutoff)
    )
    candidates = result.all()

    if not candidates:
        return None

    merchant_lower = receipt.merchant.lower()

    for cand_id, cand_merchant, cand_amount, _ in candidates:
        if cand_merchant is None or cand_amount is None:
            continue

        # Merchant overlap check: one name contains the other
        cand_lower = cand_merchant.lower()
        if merchant_lower not in cand_lower and cand_lower not in merchant_lower:
            continue

        # Amount similarity check
        max_amount = max(receipt.amount, cand_amount)
        if max_amount == 0:
            continue
        diff_pct = float(abs(receipt.amount - cand_amount) / max_amount * 100)
        if diff_pct > tolerance_pct:
            continue

        # Match found — return on first hit
        logger.warning(
            "Short-window duplicate: receipt %d matches receipt %d "
            "(merchant=%r, amount_diff=%.1f%%)",
            receipt.id, cand_id, receipt.merchant, diff_pct,
        )
        return FraudFlag(
            receipt_id=receipt.id,
            flag_type="policy_violation",
            severity=rule.severity,
            description=(
                f"Employee submitted a similar receipt (#{cand_id}) from "
                f"'{cand_merchant}' within {window_hours} hours "
                f"(rule: '{rule.rule_name}')."
            ),
            details={
                "rule_id": rule.id,
                "rule_name": rule.rule_name,
                "matching_receipt_id": int(cand_id),
                "merchant": receipt.merchant,
                "amount_diff_pct": round(diff_pct, 2),
                "window_hours": window_hours,
            },
            confidence_score=Decimal("80.00"),
        )

    return None
