import pytest

from app.services.policy_rules import (
    KNOWN_RULE_TYPES,
    PolicyRuleError,
    validate_parameters,
)


def test_amount_limit_accepts_limit_and_optional_category():
    out = validate_parameters("amount_limit", {"limit": 75, "category": " Meals "})
    assert out == {"limit": 75, "category": "meals"}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"limit": 0},
        {"limit": -1},
        {"limit": 1_000_001},
        {"limit": "75"},
        {"limit": True},
        {"limit": float("inf")},
    ],
)
def test_amount_limit_rejects_missing_zero_negative_huge_string_bool_and_inf(params):
    with pytest.raises(PolicyRuleError) as exc:
        validate_parameters("amount_limit", params)
    assert "limit" in str(exc.value)


def test_amount_limit_boundary_values():
    assert validate_parameters("amount_limit", {"limit": 0.01})["limit"] == 0.01
    assert validate_parameters("amount_limit", {"limit": 1_000_000})["limit"] == 1_000_000


def test_amount_limit_rejects_nan_and_bad_category():
    with pytest.raises(PolicyRuleError):
        validate_parameters("amount_limit", {"limit": float("nan")})
    with pytest.raises(PolicyRuleError, match="category"):
        validate_parameters("amount_limit", {"limit": 5, "category": "   "})
    with pytest.raises(PolicyRuleError, match="category"):
        validate_parameters("amount_limit", {"limit": 5, "category": "x" * 51})


def test_future_date_requires_empty_parameters():
    assert validate_parameters("future_date", {}) == {}
    with pytest.raises(PolicyRuleError):
        validate_parameters("future_date", {"x": 1})


def test_vendor_category_cleans_and_dedupes_keywords():
    out = validate_parameters(
        "vendor_category",
        {"allowed_categories": {" Meals ": [" Cafe ", "cafe", "Diner"]}},
    )
    assert out == {"allowed_categories": {"meals": ["cafe", "diner"]}}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"allowed_categories": {}},
        {"allowed_categories": {"meals": []}},
        {"allowed_categories": {"  ": ["cafe"]}},
        {"allowed_categories": {"meals": ["  "]}},
        {"allowed_categories": {"meals": [1]}},
        {"allowed_categories": {"meals": [f"k{i}" for i in range(101)]}},
    ],
)
def test_vendor_category_rejects_empty_categories_empty_keyword_lists_blank_strings_and_over_100_keywords(params):
    with pytest.raises(PolicyRuleError) as exc:
        validate_parameters("vendor_category", params)
    assert "allowed_categories" in str(exc.value)


def test_round_number_bounds():
    assert validate_parameters("round_number", {"min_amount": 10}) == {"min_amount": 10}
    for bad in (0, -5, "10"):
        with pytest.raises(PolicyRuleError, match="min_amount"):
            validate_parameters("round_number", {"min_amount": bad})


def test_short_window_duplicate_bounds():
    def ok(w, t):
        return validate_parameters(
            "short_window_duplicate", {"window_hours": w, "amount_tolerance_pct": t}
        )

    assert ok(1, 0) == {"window_hours": 1, "amount_tolerance_pct": 0}
    assert ok(720, 100)["window_hours"] == 720
    for w in (0, 721, 1.5, True):
        with pytest.raises(PolicyRuleError, match="window_hours"):
            ok(w, 10)
    for t in (-1, 100.5):
        with pytest.raises(PolicyRuleError, match="amount_tolerance_pct"):
            ok(48, t)
    with pytest.raises(PolicyRuleError, match="window_hours"):
        validate_parameters("short_window_duplicate", {"amount_tolerance_pct": 10})
    with pytest.raises(PolicyRuleError, match="amount_tolerance_pct"):
        validate_parameters("short_window_duplicate", {"window_hours": 48})


def test_unknown_type_raises_unknown_rule_type_message():
    with pytest.raises(PolicyRuleError, match="Unknown rule type 'nope'"):
        validate_parameters("nope", {})


def test_known_rule_types_has_the_five_types():
    assert KNOWN_RULE_TYPES == frozenset(
        {
            "amount_limit",
            "future_date",
            "vendor_category",
            "round_number",
            "short_window_duplicate",
        }
    )


@pytest.mark.parametrize("huge", [10**400, -(10**400)])
def test_huge_integers_raise_policy_rule_error_not_overflow(huge):
    with pytest.raises(PolicyRuleError, match="limit"):
        validate_parameters("amount_limit", {"limit": huge})
    with pytest.raises(PolicyRuleError, match="min_amount"):
        validate_parameters("round_number", {"min_amount": huge})
    with pytest.raises(PolicyRuleError, match="window_hours"):
        validate_parameters(
            "short_window_duplicate",
            {"window_hours": huge, "amount_tolerance_pct": 10},
        )
    with pytest.raises(PolicyRuleError, match="amount_tolerance_pct"):
        validate_parameters(
            "short_window_duplicate",
            {"window_hours": 48, "amount_tolerance_pct": huge},
        )
