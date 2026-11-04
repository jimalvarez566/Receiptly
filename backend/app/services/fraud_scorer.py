import logging
from decimal import Decimal

from app.models.fraud_flag import FraudFlag

logger = logging.getLogger(__name__)

# Base score contributed by each flag, keyed by (flag_type, severity).
# These represent the maximum risk contribution before confidence weighting.
_BASE_SCORES: dict[tuple[str, str], int] = {
    ("duplicate",        "high"):   75,
    ("duplicate",        "medium"): 55,
    ("duplicate",        "low"):    35,
    ("policy_violation", "high"):   50,
    ("policy_violation", "medium"): 30,
    ("policy_violation", "low"):    15,
    # Placeholders until anomaly thresholds are tuned; unused while a rule is shadowed
    ("anomaly",          "high"):   50,
    ("anomaly",          "medium"): 30,
    ("anomaly",          "low"):    15,
}

# Fallback when a (type, severity) combo isn't in the table above
_DEFAULT_BASE_SCORE = 20

# Risk level thresholds
_THRESHOLDS = [
    (70, "high"),
    (30, "medium"),
    (0,  "low"),
]


def is_shadow(flag: FraudFlag) -> bool:
    """Whether the flag is from a rule in shadow mode.

    Shadow flags are stored and shown to reviewers but add nothing to the score
    and don't change a receipt's status, so a new signal's false-positive rate
    can be measured before it counts (see ``anomaly_detector``).
    """
    return bool((flag.details or {}).get("shadow"))


def calculate_score(flags: list[FraudFlag]) -> tuple[int, str]:
    """Combine fraud flags into a 0–100 risk score and a risk level string.

    Shadow flags (``is_shadow``) are ignored.

    Algorithm:
    1. Compute an individual score for each flag:
           base_score * (confidence / 100)
       where base_score is looked up by (flag_type, severity).
    2. Sort individual scores descending.
    3. Sum with diminishing returns: each successive flag contributes half
       the weight of the previous one. This prevents trivial multi-flag
       receipts from always pinning at 100, while still pushing the score
       meaningfully higher when multiple serious signals coincide.
    4. Clamp to [0, 100] and round to the nearest integer.

    Examples:
        one duplicate (high, conf=100) → 75 → "high"
        one policy violation (medium, conf=85) → 25 → "low"
        duplicate (high) + policy violation (medium) → 75 + 15 = 90 → "high"
        three low policy violations → 15 + 7 + 3 = 25 → "low"

    Returns:
        (fraud_score, risk_level) — e.g. (75, "high")
    """
    flags = [f for f in flags if not is_shadow(f)]
    if not flags:
        return 0, "low"

    individual_scores: list[float] = []
    for flag in flags:
        base = _BASE_SCORES.get((flag.flag_type, flag.severity), _DEFAULT_BASE_SCORE)
        confidence = float(flag.confidence_score or Decimal("100"))
        individual_scores.append(base * confidence / 100)

    individual_scores.sort(reverse=True)

    total = 0.0
    weight = 1.0
    for score in individual_scores:
        total += score * weight
        weight *= 0.5  # each additional flag contributes half as much

    fraud_score = min(100, round(total))

    risk_level = "low"
    for threshold, level in _THRESHOLDS:
        if fraud_score >= threshold:
            risk_level = level
            break

    logger.info(
        "Fraud score calculated: %d (%s) from %d flag(s)",
        fraud_score, risk_level, len(flags),
    )
    return fraud_score, risk_level
