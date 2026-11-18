"""Rule-type registry and parameter validation for company policy rules.

Pure module (no DB). Each known rule type maps to a validator that returns
cleaned parameters or raises PolicyRuleError with a readable message that
starts with the offending setting's name.
"""
import math
from typing import Callable

MAX_AMOUNT = 1_000_000
MAX_CATEGORY_LEN = 50
MAX_KEYWORDS_PER_CATEGORY = 100


class PolicyRuleError(ValueError):
    """Raised when rule parameters are invalid; message is user-readable."""


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (isinstance(value, int) or math.isfinite(value))
    )


def _amount(params: dict, key: str):
    value = params.get(key)
    if not _is_number(value) or value <= 0 or value > MAX_AMOUNT:
        raise PolicyRuleError(
            f"{key} must be a number greater than 0 and at most 1,000,000"
        )
    return value


def _clean_name(value, label: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyRuleError(f"{label} must be a non-empty text value")
    cleaned = value.strip().lower()
    if len(cleaned) > max_len:
        raise PolicyRuleError(f"{label} must be at most {max_len} characters")
    return cleaned


def _only_keys(params: dict, allowed: set[str]) -> None:
    if not isinstance(params, dict):
        raise PolicyRuleError("parameters must be an object")
    extra = sorted(set(params) - allowed)
    if extra:
        raise PolicyRuleError(f"{extra[0]} is not a setting for this rule type")


def _validate_amount_limit(params: dict) -> dict:
    _only_keys(params, {"limit", "category"})
    out = {"limit": _amount(params, "limit")}
    if params.get("category") is not None:
        out["category"] = _clean_name(
            params["category"], "category", MAX_CATEGORY_LEN
        )
    return out


def _validate_future_date(params: dict) -> dict:
    _only_keys(params, set())
    return {}


def _validate_vendor_category(params: dict) -> dict:
    _only_keys(params, {"allowed_categories"})
    cats = params.get("allowed_categories")
    if not isinstance(cats, dict) or not cats:
        raise PolicyRuleError(
            "allowed_categories must include at least one category"
        )
    cleaned: dict[str, list[str]] = {}
    for raw_name, raw_keywords in cats.items():
        name = _clean_name(raw_name, "allowed_categories category name", MAX_CATEGORY_LEN)
        if (
            not isinstance(raw_keywords, list)
            or not raw_keywords
            or len(raw_keywords) > MAX_KEYWORDS_PER_CATEGORY
        ):
            raise PolicyRuleError(
                f"allowed_categories '{name}' must have 1 to "
                f"{MAX_KEYWORDS_PER_CATEGORY} keywords"
            )
        keywords = cleaned.setdefault(name, [])
        for kw in raw_keywords:
            kw = _clean_name(kw, f"allowed_categories '{name}' keyword", 100)
            if kw not in keywords:
                keywords.append(kw)
    if len(cleaned) > 0 and any(len(v) > MAX_KEYWORDS_PER_CATEGORY for v in cleaned.values()):
        raise PolicyRuleError(
            f"allowed_categories must have at most {MAX_KEYWORDS_PER_CATEGORY} keywords per category"
        )
    return {"allowed_categories": cleaned}


def _validate_round_number(params: dict) -> dict:
    _only_keys(params, {"min_amount"})
    return {"min_amount": _amount(params, "min_amount")}


def _validate_short_window_duplicate(params: dict) -> dict:
    _only_keys(params, {"window_hours", "amount_tolerance_pct"})
    hours = params.get("window_hours")
    if (
        not isinstance(hours, int)
        or isinstance(hours, bool)
        or not 1 <= hours <= 720
    ):
        raise PolicyRuleError("window_hours must be a whole number from 1 to 720")
    pct = params.get("amount_tolerance_pct")
    if not _is_number(pct) or not 0 <= pct <= 100:
        raise PolicyRuleError("amount_tolerance_pct must be a number from 0 to 100")
    return {"window_hours": hours, "amount_tolerance_pct": pct}


_VALIDATORS: dict[str, Callable[[dict], dict]] = {
    "amount_limit": _validate_amount_limit,
    "future_date": _validate_future_date,
    "vendor_category": _validate_vendor_category,
    "round_number": _validate_round_number,
    "short_window_duplicate": _validate_short_window_duplicate,
}

KNOWN_RULE_TYPES: frozenset[str] = frozenset(_VALIDATORS)


def validate_parameters(rule_type: str, parameters: dict) -> dict:
    """Return cleaned parameters for rule_type or raise PolicyRuleError."""
    validator = _VALIDATORS.get(rule_type)
    if validator is None:
        raise PolicyRuleError(f"Unknown rule type '{rule_type}'")
    return validator(parameters)
