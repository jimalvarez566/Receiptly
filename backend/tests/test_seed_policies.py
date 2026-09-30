import pytest

import seed_policies


def test_parse_args_requires_tenant_id():
    with pytest.raises(SystemExit):
        seed_policies.parse_args([])


def test_parse_args_reads_tenant_id():
    ns = seed_policies.parse_args(["--tenant-id", "5"])
    assert ns.tenant_id == 5


def test_module_uses_shared_default_rules():
    from app.services.policy_defaults import DEFAULT_RULES
    assert seed_policies.DEFAULT_RULES is DEFAULT_RULES
