import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Membership, Tenant
from app.models.employee import Employee
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt
from app.services.duplicate_detector import detect_duplicates
from app.services.policy_validator import validate_receipt


async def _tenant(db_session, name):
    t = Tenant(name=name, created_by_user_id=uuid.uuid4())
    db_session.add(t)
    await db_session.flush()
    db_session.add(Membership(tenant_id=t.id, user_id=t.created_by_user_id, role="owner"))
    await db_session.flush()
    return t


async def _receipt(db_session, tenant_id, **kw):
    r = Receipt(
        image_path="x",
        tenant_id=tenant_id,
        submitted_by_user_id=uuid.uuid4(),
        status="pending",
        **kw,
    )
    db_session.add(r)
    await db_session.flush()
    return r


async def test_policy_rules_do_not_cross_tenants(db_session):
    a = await _tenant(db_session, "A")
    b = await _tenant(db_session, "B")
    # Tenant A has a strict amount limit; Tenant B has none.
    db_session.add(PolicyRule(
        tenant_id=a.id, rule_name="cap", rule_type="amount_limit",
        severity="high", parameters={"limit": 10}, is_active=True,
    ))
    await db_session.flush()

    r_b = await _receipt(db_session, b.id, amount=Decimal("500.00"), merchant="Widgets Inc")
    flags_b = await validate_receipt(r_b, db_session)
    assert flags_b == []  # A's rule must not touch B's receipt

    r_a = await _receipt(db_session, a.id, amount=Decimal("500.00"), merchant="Widgets Inc")
    flags_a = await validate_receipt(r_a, db_session)
    assert any(f.flag_type == "policy_violation" for f in flags_a)


async def test_short_window_duplicate_does_not_cross_tenants(db_session):
    a = await _tenant(db_session, "A")
    b = await _tenant(db_session, "B")
    rule_params = {"window_hours": 48, "amount_tolerance_pct": 10}
    db_session.add(PolicyRule(
        tenant_id=b.id, rule_name="swd", rule_type="short_window_duplicate",
        severity="high", parameters=rule_params, is_active=True,
    ))
    await db_session.flush()

    # receipts.employee_id has an FK to employees.id; create the row and reuse
    # its id on both receipts so only the tenant filter separates them.
    emp = Employee(tenant_id=a.id, name="Sam", email="sam@a.com")
    db_session.add(emp)
    await db_session.flush()
    shared_employee = emp.id
    # Receipt.created_at maps to TIMESTAMP WITHOUT TIME ZONE, so use naive UTC.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # An older, matching receipt — but in Tenant A.
    await _receipt(
        db_session, a.id, employee_id=shared_employee, merchant="Cafe Nero",
        amount=Decimal("20.00"), created_at=now,
    )
    r_b = await _receipt(
        db_session, b.id, employee_id=shared_employee, merchant="Cafe Nero",
        amount=Decimal("20.00"), created_at=now,
    )
    flags_b = await validate_receipt(r_b, db_session)
    assert flags_b == []  # the match is in another tenant


async def test_short_window_duplicate_matches_within_same_tenant(db_session):
    b = await _tenant(db_session, "B")
    db_session.add(PolicyRule(
        tenant_id=b.id, rule_name="swd", rule_type="short_window_duplicate",
        severity="high", parameters={"window_hours": 48, "amount_tolerance_pct": 10},
        is_active=True,
    ))
    emp = Employee(tenant_id=b.id, name="E", email="e@b.com")
    db_session.add(emp)
    await db_session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    older = await _receipt(
        db_session, b.id, employee_id=emp.id, merchant="Cafe Nero",
        amount=Decimal("20.00"), created_at=now,
    )
    newer = await _receipt(
        db_session, b.id, employee_id=emp.id, merchant="Cafe Nero Downtown",
        amount=Decimal("20.50"), created_at=now,
    )
    flags = await validate_receipt(newer, db_session)
    assert len(flags) == 1
    assert flags[0].flag_type == "policy_violation"
    assert flags[0].details["matching_receipt_id"] == older.id


_HASH = "f" * 16  # a valid 64-bit hex pHash string


async def test_duplicate_detection_does_not_cross_tenants(db_session):
    a = await _tenant(db_session, "A")
    b = await _tenant(db_session, "B")
    await _receipt(db_session, a.id, image_hash=_HASH)
    r_b = await _receipt(db_session, b.id, image_hash=_HASH)

    flags = await detect_duplicates(r_b, db_session)
    assert flags == []  # A's identical image must be invisible to B


async def test_duplicate_detection_matches_within_same_tenant(db_session):
    b = await _tenant(db_session, "B")
    first = await _receipt(db_session, b.id, image_hash=_HASH)
    r_b = await _receipt(db_session, b.id, image_hash=_HASH)

    flags = await detect_duplicates(r_b, db_session)
    assert len(flags) == 1
    assert flags[0].flag_type == "duplicate"
    assert flags[0].details["duplicate_receipt_id"] == first.id
