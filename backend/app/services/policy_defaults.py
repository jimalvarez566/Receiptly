"""Default policy rules seeded for every business.

Single source of truth for both the tenants API (auto-seed on business
creation) and seed_policies.py (backfill for pre-existing businesses).
"""
import copy

from app.models.policy_rule import PolicyRule

DEFAULT_RULES: list[dict] = [
    {
        "rule_name": "Meal amount limit",
        "rule_type": "amount_limit",
        "severity": "medium",
        "parameters": {"category": "meals", "limit": 75.00},
    },
    {
        "rule_name": "Supplies amount limit",
        "rule_type": "amount_limit",
        "severity": "medium",
        "parameters": {"category": "supplies", "limit": 200.00},
    },
    {
        "rule_name": "No future dates",
        "rule_type": "future_date",
        "severity": "high",
        "parameters": {},
    },
    {
        "rule_name": "Approved expense categories",
        "rule_type": "vendor_category",
        "severity": "medium",
        "parameters": {
            "allowed_categories": {
                "meals": [
                    "restaurant", "cafe", "coffee", "starbucks", "dunkin",
                    "mcdonald", "burger", "pizza", "food", "grill", "kitchen",
                    "diner", "bistro", "bakery", "sushi", "taco", "sandwich",
                    "donut", "bagel", "smoothie", "juice", "bar & grill",
                ],
                "travel": [
                    "hotel", "inn", "suites", "marriott", "hilton", "hyatt",
                    "sheraton", "westin", "airbnb", "delta", "united",
                    "american airlines", "southwest", "jetblue", "spirit",
                    "frontier", "alaska airlines", "uber", "lyft", "taxi",
                    "hertz", "enterprise", "avis", "budget", "national",
                    "amtrak", "greyhound", "parking", "toll",
                ],
                "supplies": [
                    "staples", "office depot", "office max", "amazon",
                    "best buy", "costco", "walmart", "target", "home depot",
                    "fedex", "ups", "usps", "post office", "print", "ink",
                    "paper", "notebook", "pen", "binder",
                ],
            }
        },
    },
    {
        "rule_name": "Round number detection",
        "rule_type": "round_number",
        "severity": "low",
        "parameters": {"min_amount": 10.00},
    },
    {
        "rule_name": "Short window duplicate",
        "rule_type": "short_window_duplicate",
        "severity": "high",
        "parameters": {"window_hours": 48, "amount_tolerance_pct": 10},
    },
]


def build_default_rules(tenant_id: int) -> list[PolicyRule]:
    """Return fresh, unsaved PolicyRule objects for the given tenant."""
    return [
        PolicyRule(
            tenant_id=tenant_id,
            rule_name=d["rule_name"],
            rule_type=d["rule_type"],
            severity=d["severity"],
            parameters=copy.deepcopy(d["parameters"]),
            is_active=True,
        )
        for d in DEFAULT_RULES
    ]
