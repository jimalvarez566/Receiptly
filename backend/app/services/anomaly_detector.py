"""History-based anomaly signals, compared across a business's past receipts.

Two rule types, configured per business in ``policy_rules`` alongside the
policy checks (defaults in ``policy_defaults.py``):

* ``amount_outlier`` — this employee's spend for the week or month (one rule
  per period) is far above their usual week or month. Employees with too
  little history are compared with the whole business instead.
* ``content_match`` — an earlier receipt in the business has the same
  merchant, transaction date and total: from another employee, one expense
  claimed twice; from the same employee, a receipt resubmitted with a new
  photo, which perceptual hashing misses.

The employee is the submitting user, since every member submits their own
receipts. Only receipts submitted before this one count as history, so
re-analyzing a receipt later gives the same answer.

Rules run in shadow mode unless their parameters say ``"shadow": false``: the
flag is stored and shown, but adds nothing to the fraud score and doesn't
change the receipt's status (see ``fraud_scorer.is_shadow``). Reviewer
decisions on shadowed receipts then measure a signal's false-positive rate
before it is trusted.
"""

import logging
import math
import re
import statistics
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fraud_flag import FraudFlag
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt

logger = logging.getLogger(__name__)

RULE_TYPES = ("amount_outlier", "content_match")

# Calendar periods an amount_outlier rule can total over
_PERIODS = ("week", "month")

# Most recent period totals used for a baseline; bounds the query for
# businesses with long histories and lets the baseline follow recent spending.
_MAX_HISTORY = 500

# Robust z-score constants (Iglewicz & Hoaglin): 0.6745 scales MAD to a
# standard deviation for normal data; 1.253314 does the same for mean absolute
# deviation, used when MAD is 0 because over half the totals are identical.
_MAD_SCALE = 0.6745
_MEAN_AD_SCALE = 1.253314


async def detect_anomalies(
    receipt: Receipt,
    db: AsyncSession,
    exclude_receipt_ids: frozenset[int] = frozenset(),
) -> list[FraudFlag]:
    """Run the business's active anomaly rules against its earlier receipts.

    ``exclude_receipt_ids`` are receipts already flagged as image duplicates of
    this one; content matching skips them so one resubmission isn't flagged
    twice on the same evidence.

    Returns a list of FraudFlag ORM objects (not yet added to the session).
    """
    result = await db.execute(
        select(PolicyRule)
        .where(PolicyRule.is_active == True)  # noqa: E712
        .where(PolicyRule.tenant_id == receipt.tenant_id)
        .where(PolicyRule.rule_type.in_(RULE_TYPES))
    )
    rules = result.scalars().all()

    flags: list[FraudFlag] = []

    for rule in rules:
        try:
            if rule.rule_type == "amount_outlier":
                flag = await _check_amount_outlier(rule, receipt, db)
                new_flags = [flag] if flag else []
            elif rule.rule_type == "content_match":
                new_flags = await _check_content_match(rule, receipt, db, exclude_receipt_ids)
        except Exception:
            logger.exception("Error evaluating rule '%s' (id=%d)", rule.rule_name, rule.id)
            continue

        if rule.parameters.get("shadow", True):
            for flag in new_flags:
                flag.details["shadow"] = True
        flags.extend(new_flags)

    return flags


def _earlier_in_business(receipt: Receipt) -> tuple:
    """Filters selecting receipts in the same business submitted before this one."""
    # Receipt.created_at is stored as a naive UTC timestamp; an unsaved receipt
    # counts as submitted now.
    cutoff = receipt.created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    return (
        Receipt.tenant_id == receipt.tenant_id,
        Receipt.id != receipt.id,
        Receipt.created_at < cutoff,
    )


# ---------------------------------------------------------------------------
# amount_outlier
# ---------------------------------------------------------------------------

async def _check_amount_outlier(
    rule: PolicyRule,
    receipt: Receipt,
    db: AsyncSession,
) -> FraudFlag | None:
    """Flag a week or month in which this employee spent far more than usual.

    ``period`` is "week" (Monday to Sunday) or "month", by transaction date;
    businesses get one rule for each. The period's spend is this receipt plus
    the employee's earlier receipts dated in the same period, compared with
    their spend in other periods (periods with no receipts aren't counted).

    Distance from normal is a robust z-score (distance from the median in units
    of median absolute deviation), so one earlier outlier doesn't mask the
    next. Employees with fewer than ``min_history`` other periods are compared
    with every employee's periods in the business; if the business has fewer
    than ``min_company_history``, the rule doesn't fire. Rejected receipts are
    left out of every total, so fraud doesn't define normal spending.

    Fires when both hold:
    - robust z-score >= ``threshold``
    - period spend >= ``min_ratio`` x the baseline median, so a $15 week isn't
      flagged against a history of near-identical $5 weeks

    Parameters: {"period": "week", "threshold": 3.5, "min_ratio": 2.0,
                 "min_history": 5, "min_company_history": 10, "shadow": true}
    """
    if receipt.amount is None or receipt.transaction_date is None:
        return None

    params = rule.parameters
    period = params.get("period")
    if period not in _PERIODS:
        logger.warning("Rule '%s' has no valid 'period' (week or month); skipping", rule.rule_name)
        return None
    threshold = float(params.get("threshold", 3.5))
    min_ratio = float(params.get("min_ratio", 2.0))
    min_history = int(params.get("min_history", 5))
    min_company_history = int(params.get("min_company_history", 10))

    start = _period_start(period, receipt.transaction_date)
    totals = await _period_totals(receipt, db, period, same_submitter=True)
    spend = float(receipt.amount) + sum(t for _, s, t in totals if s == start)

    baseline = "employee"
    history = [t for _, s, t in totals if s != start]
    if len(history) < min_history:
        baseline = "business"
        totals = await _period_totals(receipt, db, period, same_submitter=False)
        history = [t for _, s, t in totals if s != start]
        if len(history) < min_company_history:
            return None

    median = statistics.median(history)
    if median <= 0:
        return None

    z = _robust_z(spend, history, median)
    ratio = spend / median
    if z < threshold or ratio < min_ratio:
        return None

    label = f"the week of {start}" if period == "week" else start.strftime("%B %Y")
    typical = (
        f"their typical {period} (median ${median:.2f} over {len(history)} other {period}s)"
        if baseline == "employee" else
        f"a typical employee {period} in this business "
        f"(median ${median:.2f} over {len(history)} employee-{period}s)"
    )
    return FraudFlag(
        receipt_id=receipt.id,
        flag_type="anomaly",
        severity=rule.severity,
        description=(
            f"With this receipt, the employee's spend for {label} is ${spend:.2f}, "
            f"{ratio:.1f}x {typical} (rule: '{rule.rule_name}')."
        ),
        details={
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "signal": "amount_outlier",
            "period": period,
            "period_start": str(start),
            "period_total": round(spend, 2),
            "baseline": baseline,
            "history_count": len(history),
            "median": round(median, 2),
            "ratio": round(ratio, 2),
            # None when every other period's total was identical (no spread to measure)
            "robust_z": round(z, 2) if math.isfinite(z) else None,
            "threshold": threshold,
            "actual_amount": float(receipt.amount),
        },
        # A business-wide baseline is weaker evidence than the employee's own
        confidence_score=Decimal("75.00") if baseline == "employee" else Decimal("60.00"),
    )


def _period_start(period: str, day: date) -> date:
    """First day of the calendar week (Monday) or month containing ``day``."""
    if period == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


async def _period_totals(
    receipt: Receipt,
    db: AsyncSession,
    period: str,
    same_submitter: bool,
) -> list[tuple[uuid.UUID, date, float]]:
    """(submitter, period start, total) of earlier, non-rejected, dated receipts,
    most recent periods first."""
    # Postgres's date_trunc('week') also starts weeks on Monday; casting the
    # date to a plain timestamp keeps the session time zone out of it.
    start = cast(func.date_trunc(period, cast(Receipt.transaction_date, DateTime)), Date)
    query = (
        select(Receipt.submitted_by_user_id, start, func.sum(Receipt.amount))
        .where(*_earlier_in_business(receipt))
        .where(Receipt.amount.is_not(None))
        .where(Receipt.transaction_date.is_not(None))
        .where(Receipt.status != "rejected")
        .group_by(Receipt.submitted_by_user_id, start)
        .order_by(start.desc())
        .limit(_MAX_HISTORY)
    )
    if same_submitter:
        query = query.where(Receipt.submitted_by_user_id == receipt.submitted_by_user_id)
    result = await db.execute(query)
    return [(submitter, s, float(total)) for submitter, s, total in result.all()]


def _robust_z(amount: float, history: list[float], median: float) -> float:
    """How far ``amount`` sits above the median, in robust standard deviations."""
    deviations = [abs(h - median) for h in history]
    mad = statistics.median(deviations)
    if mad > 0:
        return _MAD_SCALE * (amount - median) / mad
    mean_ad = statistics.fmean(deviations)
    if mean_ad > 0:
        return (amount - median) / (_MEAN_AD_SCALE * mean_ad)
    return math.inf if amount > median else 0.0


# ---------------------------------------------------------------------------
# content_match
# ---------------------------------------------------------------------------

async def _check_content_match(
    rule: PolicyRule,
    receipt: Receipt,
    db: AsyncSession,
    exclude_receipt_ids: frozenset[int],
) -> list[FraudFlag]:
    """Flag earlier receipts in the business with the same merchant, date and total.

    Date and total must match exactly. Merchants match when one normalized
    name contains the other, since OCR of two photos of one receipt rarely
    reads the name identically ("SEASIDE SUSHI" vs "Seaside Sushi House").
    Rejected receipts still count: resubmitting a rejected claim is one of the
    cases this catches.

    Returns one flag per matching receipt, like duplicate detection.
    """
    if receipt.merchant is None or receipt.amount is None or receipt.transaction_date is None:
        return []
    merchant = _normalize_merchant(receipt.merchant)
    if not merchant:
        return []

    result = await db.execute(
        select(Receipt.id, Receipt.merchant, Receipt.submitted_by_user_id)
        .where(*_earlier_in_business(receipt))
        .where(Receipt.transaction_date == receipt.transaction_date)
        .where(Receipt.amount == receipt.amount)
        .where(Receipt.merchant.is_not(None))
        .order_by(Receipt.created_at)
    )

    flags: list[FraudFlag] = []
    for cand_id, cand_merchant, cand_submitter in result.all():
        if cand_id in exclude_receipt_ids:
            continue
        other = _normalize_merchant(cand_merchant)
        if not other or (merchant not in other and other not in merchant):
            continue

        same_submitter = cand_submitter == receipt.submitted_by_user_id
        who = "which this employee submitted earlier" if same_submitter else "submitted by another employee"
        logger.warning(
            "Content match: receipt %d matches receipt %d (same_submitter=%s)",
            receipt.id, cand_id, same_submitter,
        )
        flags.append(FraudFlag(
            receipt_id=receipt.id,
            flag_type="anomaly",
            severity=rule.severity,
            description=(
                f"Same merchant, date and total as receipt #{cand_id}, {who} "
                f"('{receipt.merchant}', {receipt.transaction_date}, ${receipt.amount}) "
                f"(rule: '{rule.rule_name}')."
            ),
            details={
                "rule_id": rule.id,
                "rule_name": rule.rule_name,
                "signal": "content_match",
                "matching_receipt_id": int(cand_id),
                "same_submitter": same_submitter,
                "merchant": receipt.merchant,
                "transaction_date": str(receipt.transaction_date),
                "actual_amount": float(receipt.amount),
            },
            confidence_score=Decimal("90.00"),
        ))

    return flags


def _normalize_merchant(name: str) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())
