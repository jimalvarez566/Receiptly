import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Membership, PolicyRule, Receipt, Tenant
from app.services.policy_defaults import build_default_rules
from tests.conftest import make_token

URL = "/api/v1/policy-rules"


def _h(user_id, tenant_id):
    return {"Authorization": f"Bearer {make_token(user_id)}", "X-Tenant-ID": str(tenant_id)}


async def _seed_team(db_session, admin_id, member_id, name="AceCo"):
    tenant = Tenant(name=name, created_by_user_id=uuid.UUID(admin_id))
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(admin_id), role="admin"))
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(member_id), role="member"))
    db_session.add_all(build_default_rules(tenant.id))
    await db_session.commit()
    return tenant


async def _rules(db_session, tenant_id):
    result = await db_session.execute(
        select(PolicyRule).where(PolicyRule.tenant_id == tenant_id).order_by(PolicyRule.id)
    )
    return list(result.scalars().all())


async def _meal_rule(db_session, tenant_id):
    return next(r for r in await _rules(db_session, tenant_id) if r.rule_name == "Meal amount limit")


async def _meal_receipt(db_session, tenant, user_id):
    r = Receipt(
        image_path="x.png", tenant_id=tenant.id, submitted_by_user_id=uuid.UUID(user_id),
        status="analyzed", category="meals", merchant="cafe",
        amount=Decimal("120.00"), transaction_date=date(2024, 1, 15),
    )
    db_session.add(r)
    await db_session.commit()
    return r


def _meal_flags(body, rule_id):
    return [
        f for f in body["fraud_flags"]
        if (f.get("details") or {}).get("rule_id") == rule_id
    ]


async def test_admin_lists_the_business_rules(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.get(URL, headers=_h(user_a_id, t.id))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 6
    assert [r["id"] for r in body] == sorted(r["id"] for r in body)
    assert {"id", "rule_name", "rule_type", "severity", "parameters", "is_active", "created_at"} <= set(body[0])


async def test_member_gets_exact_403_on_all_four_endpoints(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    h = _h(user_b_id, t.id)
    resp = await client.get(URL, headers=h)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only admins can view policies"
    edit = "Only admins can edit policies"
    resp = await client.post(
        URL, headers=h,
        json={"rule_name": "x", "rule_type": "future_date", "severity": "low"},
    )
    assert (resp.status_code, resp.json()["detail"]) == (403, edit)
    resp = await client.patch(f"{URL}/99999", headers=h, json={"is_active": False})
    assert (resp.status_code, resp.json()["detail"]) == (403, edit)
    resp = await client.delete(f"{URL}/99999", headers=h)
    assert (resp.status_code, resp.json()["detail"]) == (403, edit)


@pytest.mark.parametrize(
    "rule_type,params",
    [
        ("amount_limit", {"limit": 50, "category": "Travel"}),
        ("future_date", {}),
        ("vendor_category", {"allowed_categories": {"Fuel": ["Shell"]}}),
        ("round_number", {"min_amount": 100}),
        ("short_window_duplicate", {"window_hours": 24, "amount_tolerance_pct": 5}),
    ],
)
async def test_admin_creates_rule_of_each_known_type(
    client, db_session, user_a_id, user_b_id, rule_type, params
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.post(
        URL, headers=_h(user_a_id, t.id),
        json={"rule_name": "  My rule  ", "rule_type": rule_type, "severity": "high", "parameters": params},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rule_name"] == "My rule"
    assert body["rule_type"] == rule_type
    assert body["is_active"] is True
    assert len(await _rules(db_session, t.id)) == 7


async def test_create_inactive_rule_with_is_active_false(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.post(
        URL, headers=_h(user_a_id, t.id),
        json={"rule_name": "Off", "rule_type": "future_date", "severity": "low", "is_active": False},
    )
    assert resp.status_code == 201
    assert resp.json()["is_active"] is False


async def test_create_unknown_type_is_400_with_message(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.post(
        URL, headers=_h(user_a_id, t.id),
        json={"rule_name": "x", "rule_type": "bogus", "severity": "low"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown rule type 'bogus'"


async def test_create_invalid_parameters_is_400_readable(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.post(
        URL, headers=_h(user_a_id, t.id),
        json={"rule_name": "x", "rule_type": "amount_limit", "severity": "low", "parameters": {}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("limit")


async def test_patch_updates_name_severity_limit_and_toggle(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    resp = await client.patch(
        f"{URL}/{rule.id}", headers=_h(user_a_id, t.id),
        json={
            "rule_name": " Dinner cap ", "severity": "high",
            "parameters": {"limit": 40, "category": "meals"}, "is_active": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rule_name"] == "Dinner cap"
    assert body["severity"] == "high"
    assert body["parameters"]["limit"] == 40
    assert body["is_active"] is False
    assert body["rule_type"] == "amount_limit"


async def test_patch_cannot_change_rule_type_is_422(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    resp = await client.patch(
        f"{URL}/{rule.id}", headers=_h(user_a_id, t.id), json={"rule_type": "future_date"}
    )
    assert resp.status_code == 422


async def test_patch_empty_body_is_422(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    resp = await client.patch(f"{URL}/{rule.id}", headers=_h(user_a_id, t.id), json={})
    assert resp.status_code == 422


async def test_patch_parameters_are_validated_against_stored_type(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    resp = await client.patch(
        f"{URL}/{rule.id}", headers=_h(user_a_id, t.id), json={"parameters": {}}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("limit")


async def test_patch_unknown_stored_type_allows_toggle_but_not_parameters(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = PolicyRule(
        tenant_id=t.id, rule_name="Mystery", rule_type="mystery",
        parameters={"a": 1}, severity="low",
    )
    db_session.add(rule)
    await db_session.commit()
    h = _h(user_a_id, t.id)
    resp = await client.patch(f"{URL}/{rule.id}", headers=h, json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    resp = await client.patch(f"{URL}/{rule.id}", headers=h, json={"parameters": {"a": 1}})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Parameters can't be edited for unknown rule type 'mystery'"
    resp = await client.delete(f"{URL}/{rule.id}", headers=h)
    assert resp.status_code == 204


async def test_other_business_rule_is_404_and_unchanged(client, db_session, user_a_id, user_b_id):
    t1 = await _seed_team(db_session, user_a_id, user_b_id, name="One")
    t2 = await _seed_team(db_session, user_b_id, user_a_id, name="Two")
    other = await _meal_rule(db_session, t2.id)
    h = _h(user_a_id, t1.id)
    resp = await client.patch(f"{URL}/{other.id}", headers=h, json={"is_active": False})
    assert (resp.status_code, resp.json()["detail"]) == (404, "Policy rule not found")
    resp = await client.delete(f"{URL}/{other.id}", headers=h)
    assert (resp.status_code, resp.json()["detail"]) == (404, "Policy rule not found")
    await db_session.refresh(other)
    assert other.is_active is True
    assert other.tenant_id == t2.id


async def test_delete_removes_rule_and_returns_204(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    resp = await client.delete(f"{URL}/{rule.id}", headers=_h(user_a_id, t.id))
    assert resp.status_code == 204
    assert resp.content == b""
    assert len(await _rules(db_session, t.id)) == 5


async def test_toggle_off_removes_flag_on_next_analysis(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    receipt = await _meal_receipt(db_session, t, user_b_id)
    h = _h(user_a_id, t.id)
    resp = await client.post(f"/api/v1/receipts/{receipt.id}/analyze", headers=h)
    assert resp.status_code == 200, resp.text
    assert _meal_flags(resp.json(), rule.id)
    resp = await client.patch(f"{URL}/{rule.id}", headers=h, json={"is_active": False})
    assert resp.status_code == 200
    resp = await client.post(f"/api/v1/receipts/{receipt.id}/analyze", headers=h)
    assert resp.status_code == 200
    assert not _meal_flags(resp.json(), rule.id)


async def test_delete_rule_keeps_existing_flags(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    rule = await _meal_rule(db_session, t.id)
    rule_id = rule.id
    receipt = await _meal_receipt(db_session, t, user_b_id)
    h = _h(user_a_id, t.id)
    resp = await client.post(f"/api/v1/receipts/{receipt.id}/analyze", headers=h)
    assert _meal_flags(resp.json(), rule_id)
    assert (await client.delete(f"{URL}/{rule_id}", headers=h)).status_code == 204
    resp = await client.get(f"/api/v1/receipts/{receipt.id}", headers=h)
    assert resp.status_code == 200
    assert _meal_flags(resp.json(), rule_id)
