from app.models.policy_rule import PolicyRule
from app.services.policy_defaults import DEFAULT_RULES, build_default_rules


def test_default_rules_shape():
    assert len(DEFAULT_RULES) == 6
    names = {r["rule_name"] for r in DEFAULT_RULES}
    assert "No future dates" in names
    for r in DEFAULT_RULES:
        assert set(r) == {"rule_name", "rule_type", "severity", "parameters"}
        assert isinstance(r["parameters"], dict)


def test_build_default_rules_sets_tenant_and_active():
    rules = build_default_rules(tenant_id=42)
    assert len(rules) == 6
    assert all(isinstance(r, PolicyRule) for r in rules)
    assert all(r.tenant_id == 42 for r in rules)
    assert all(r.is_active is True for r in rules)
    assert {r.rule_name for r in rules} == {d["rule_name"] for d in DEFAULT_RULES}


def test_build_default_rules_returns_fresh_objects_each_call():
    a = build_default_rules(1)
    b = build_default_rules(1)
    assert a[0] is not b[0]


def test_build_default_rules_deep_copies_parameters():
    rules = build_default_rules(1)
    by_name = {r.rule_name: r for r in rules}
    for d in DEFAULT_RULES:
        assert by_name[d["rule_name"]].parameters == d["parameters"]
        assert by_name[d["rule_name"]].parameters is not d["parameters"]
