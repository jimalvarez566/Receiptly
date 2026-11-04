"""Tests for anomaly_detector and how shadow-mode flags flow through scoring.

DB-backed: every signal compares a receipt with the business's earlier receipts.
"""
import itertools
import logging
import math
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Membership, Tenant
from app.models.fraud_flag import FraudFlag
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt
from app.services.anomaly_detector import _period_start, _robust_z, detect_anomalies
from app.services.fraud_detection_pipeline import run_fraud_pipeline
from app.services.fraud_scorer import calculate_score, is_shadow
from app.services.policy_validator import validate_receipt
from tests.conftest import make_token

WEEKLY = ("amount_outlier", {"period": "week", "threshold": 3.5, "min_ratio": 2.0,
                             "min_history": 5, "min_company_history": 10})
MONTHLY = ("amount_outlier", {**WEEKLY[1], "period": "month"})
MATCH = ("content_match", {})

# An employee's usual spend per period: median $20.55, little spread
USUAL = [18.50, 21.00, 19.75, 22.40, 20.10, 23.00]

_DAY = date(2026, 9, 30)  # a Wednesday
# Receipt.created_at is naive UTC; each test receipt is submitted a minute after the last
_clock = (datetime(2026, 10, 1, 9, 0) + timedelta(minutes=i) for i in itertools.count())


async def _business(db, *rules, name="Acme"):
    tenant = Tenant(name=name, created_by_user_id=uuid.uuid4())
    db.add(tenant)
    await db.flush()
    for rule_type, params in rules:
        db.add(PolicyRule(
            tenant_id=tenant.id, rule_name=f"test {rule_type}", rule_type=rule_type,
            severity="medium", parameters=params, is_active=True,
        ))
    await db.flush()
    return tenant


async def _receipt(db, tenant, user, amount, *, merchant="Cafe Nero", day=_DAY,
                   status="analyzed", image_hash=None):
    r = Receipt(
        image_path="x", tenant_id=tenant.id, submitted_by_user_id=user,
        merchant=merchant, amount=Decimal(str(amount)), transaction_date=day,
        status=status, image_hash=image_hash, created_at=next(_clock),
    )
    db.add(r)
    await db.flush()
    return r


def _periods_ago(period, n):
    """A day ``n`` weeks or months before _DAY."""
    if period == "week":
        return _DAY - timedelta(weeks=n)
    year, month = divmod(_DAY.year * 12 + _DAY.month - 1 - n, 12)
    return date(year, month + 1, 15)


async def _history(db, tenant, user, amounts, period="week", **kw):
    """One earlier receipt in each period before _DAY's, most recent first."""
    for i, amount in enumerate(amounts):
        await _receipt(db, tenant, user, amount, day=_periods_ago(period, i + 1), **kw)


# ---------------------------------------------------------------------------
# amount_outlier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [WEEKLY, MONTHLY], ids=["week", "month"])
async def test_amount_outlier_flags_period_far_above_employee_history(db_session, rule):
    period = rule[1]["period"]
    biz = await _business(db_session, rule)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL, period)
    # Each receipt is ordinary; together they are ~2.9x a usual period
    first = await _receipt(db_session, biz, alice, 30.00)
    r = await _receipt(db_session, biz, alice, 30.00)

    assert await detect_anomalies(first, db_session) == []
    [flag] = await detect_anomalies(r, db_session)
    assert flag.flag_type == "anomaly"
    assert flag.details["signal"] == "amount_outlier"
    assert flag.details["period"] == period
    assert flag.details["period_start"] == str(_period_start(period, _DAY))
    assert flag.details["period_total"] == 60.00
    assert flag.details["actual_amount"] == 30.00
    assert flag.details["baseline"] == "employee"
    assert flag.details["history_count"] == len(USUAL)
    assert flag.confidence_score == Decimal("75.00")
    assert f"their typical {period}" in flag.description


async def test_amount_outlier_passes_usual_period(db_session):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL)
    await _receipt(db_session, biz, alice, 10.00)
    r = await _receipt(db_session, biz, alice, 14.00)
    assert await detect_anomalies(r, db_session) == []


@pytest.mark.parametrize("earlier_weeks, this_week, flagged", [
    (20.00, 24.00, set()),
    (20.00, 60.00, {"week"}),            # one heavy week in an ordinary month
    (38.00, 38.00, {"month"}),           # every week a little high
    (20.00, 200.00, {"week", "month"}),
])
async def test_weekly_and_monthly_rules_flag_separately(db_session, earlier_weeks, this_week, flagged):
    biz = await _business(db_session, WEEKLY, MONTHLY)
    alice = uuid.uuid4()
    # A receipt every week since March: ~$21 weeks and ~$85 months. Weeks
    # earlier in this month cost `earlier_weeks` each.
    for weeks_ago in range(30, 0, -1):
        day = _DAY - timedelta(weeks=weeks_ago)
        amount = earlier_weeks if day.month == _DAY.month else USUAL[weeks_ago % len(USUAL)]
        await _receipt(db_session, biz, alice, amount, day=day)
    r = await _receipt(db_session, biz, alice, this_week)

    flags = await detect_anomalies(r, db_session)
    assert sorted(f.details["period"] for f in flags) == sorted(flagged)


async def test_amount_outlier_weeks_run_monday_to_sunday(db_session):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL)
    monday = _period_start("week", _DAY)
    await _receipt(db_session, biz, alice, 30.00, day=monday - timedelta(days=1))  # last week
    await _receipt(db_session, biz, alice, 30.00, day=monday)
    r = await _receipt(db_session, biz, alice, 30.00, day=monday + timedelta(days=6))

    [flag] = await detect_anomalies(r, db_session)
    assert flag.details["period_start"] == str(monday)
    assert flag.details["period_total"] == 60.00


def test_period_start():
    assert _period_start("week", date(2026, 10, 4)) == date(2026, 9, 28)  # Sunday -> Monday
    assert _period_start("week", date(2026, 9, 28)) == date(2026, 9, 28)
    assert _period_start("month", date(2026, 9, 30)) == date(2026, 9, 1)


async def test_amount_outlier_falls_back_to_business_for_new_employee(db_session):
    biz = await _business(db_session, WEEKLY)
    for amounts in (USUAL, USUAL[:4]):
        await _history(db_session, biz, uuid.uuid4(), amounts)
    newcomer = uuid.uuid4()
    await _receipt(db_session, biz, newcomer, 20.00, day=_periods_ago("week", 4))
    r = await _receipt(db_session, biz, newcomer, 180.00)

    [flag] = await detect_anomalies(r, db_session)
    assert flag.details["baseline"] == "business"
    assert flag.details["history_count"] == len(USUAL) + 4 + 1
    assert flag.confidence_score == Decimal("60.00")
    assert "typical employee week in this business" in flag.description


async def test_amount_outlier_needs_enough_history(db_session):
    biz = await _business(db_session, WEEKLY)
    await _history(db_session, biz, uuid.uuid4(), USUAL[:3])
    r = await _receipt(db_session, biz, uuid.uuid4(), 500.00)
    assert await detect_anomalies(r, db_session) == []


async def test_amount_outlier_leaves_rejected_receipts_out_of_totals(db_session):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL)
    # Counted, these would make a usual week ~$220 and hide this one
    await _history(db_session, biz, alice, [200.00] * 6, status="rejected")
    await _receipt(db_session, biz, alice, 200.00, status="rejected")
    r = await _receipt(db_session, biz, alice, 60.00)

    [flag] = await detect_anomalies(r, db_session)
    assert flag.details["median"] == pytest.approx(20.55)
    assert flag.details["period_total"] == 60.00


async def test_amount_outlier_only_uses_earlier_receipts(db_session):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    r = await _receipt(db_session, biz, alice, 60.00)
    await _history(db_session, biz, alice, USUAL)  # submitted afterwards
    assert await detect_anomalies(r, db_session) == []


@pytest.mark.parametrize("amount, flagged", [(8.00, False), (12.00, True)])
async def test_amount_outlier_needs_min_ratio_when_history_has_no_spread(db_session, amount, flagged):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, [5.00] * 6)
    r = await _receipt(db_session, biz, alice, amount)

    # Any increase is infinitely many deviations out, so only min_ratio (2x $5) decides
    flags = await detect_anomalies(r, db_session)
    assert bool(flags) == flagged
    if flagged:
        assert flags[0].details["robust_z"] is None


async def test_amount_outlier_ignores_other_businesses(db_session):
    biz = await _business(db_session, WEEKLY)
    other = await _business(db_session, name="Other")
    alice = uuid.uuid4()
    await _history(db_session, other, alice, USUAL * 2)
    r = await _receipt(db_session, biz, alice, 180.00)
    assert await detect_anomalies(r, db_session) == []


async def test_amount_outlier_skips_undated_receipt(db_session):
    biz = await _business(db_session, WEEKLY)
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL)
    r = await _receipt(db_session, biz, alice, 180.00, day=None)
    assert await detect_anomalies(r, db_session) == []


async def test_amount_outlier_needs_a_period(db_session, caplog):
    no_period = {k: v for k, v in WEEKLY[1].items() if k != "period"}
    biz = await _business(db_session, ("amount_outlier", no_period))
    alice = uuid.uuid4()
    await _history(db_session, biz, alice, USUAL)
    r = await _receipt(db_session, biz, alice, 180.00)
    with caplog.at_level(logging.WARNING):
        assert await detect_anomalies(r, db_session) == []
    assert "no valid 'period'" in caplog.text


def test_robust_z():
    assert _robust_z(20, [10, 12, 14], 12) == pytest.approx(0.6745 * 8 / 2)
    # MAD is 0, so mean absolute deviation is used instead
    assert _robust_z(9, [5, 5, 5, 9], 5) == pytest.approx(4 / 1.253314)
    assert _robust_z(6, [5, 5, 5], 5) == math.inf
    assert _robust_z(5, [5, 5, 5], 5) == 0.0


# ---------------------------------------------------------------------------
# content_match
# ---------------------------------------------------------------------------

async def test_content_match_flags_same_receipt_from_another_employee(db_session):
    biz = await _business(db_session, MATCH)
    first = await _receipt(db_session, biz, uuid.uuid4(), 70.85, merchant="Seaside Sushi House")
    # A second photo of the same receipt rarely OCRs the name identically
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85, merchant="SEASIDE SUSHI")

    [flag] = await detect_anomalies(r, db_session)
    assert flag.flag_type == "anomaly"
    assert flag.details["signal"] == "content_match"
    assert flag.details["matching_receipt_id"] == first.id
    assert flag.details["same_submitter"] is False
    assert "another employee" in flag.description


async def test_content_match_flags_resubmission_by_same_employee(db_session):
    biz = await _business(db_session, MATCH)
    alice = uuid.uuid4()
    await _receipt(db_session, biz, alice, 70.85)
    r = await _receipt(db_session, biz, alice, 70.85)

    [flag] = await detect_anomalies(r, db_session)
    assert flag.details["same_submitter"] is True
    assert "this employee submitted earlier" in flag.description


@pytest.mark.parametrize("change", [
    {"amount": 70.86},
    {"day": _DAY - timedelta(days=1)},
    {"merchant": "Harbor Grill"},
])
async def test_content_match_needs_same_merchant_date_and_total(db_session, change):
    biz = await _business(db_session, MATCH)
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    kw = {"amount": 70.85, **change}
    r = await _receipt(db_session, biz, uuid.uuid4(), kw.pop("amount"), **kw)
    assert await detect_anomalies(r, db_session) == []


async def test_content_match_counts_rejected_receipts(db_session):
    biz = await _business(db_session, MATCH)
    alice = uuid.uuid4()
    first = await _receipt(db_session, biz, alice, 70.85, status="rejected")
    r = await _receipt(db_session, biz, alice, 70.85)

    [flag] = await detect_anomalies(r, db_session)
    assert flag.details["matching_receipt_id"] == first.id


async def test_content_match_skips_excluded_receipts(db_session):
    biz = await _business(db_session, MATCH)
    first = await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    assert await detect_anomalies(r, db_session, exclude_receipt_ids=frozenset({first.id})) == []


async def test_content_match_ignores_other_businesses(db_session):
    biz = await _business(db_session, MATCH)
    other = await _business(db_session, name="Other")
    await _receipt(db_session, other, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    assert await detect_anomalies(r, db_session) == []


# ---------------------------------------------------------------------------
# Shadow mode, rule selection, and scoring
# ---------------------------------------------------------------------------

async def test_rules_default_to_shadow_mode(db_session):
    biz = await _business(db_session, MATCH)  # parameters don't mention shadow
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)

    [flag] = await detect_anomalies(r, db_session)
    assert is_shadow(flag)


async def test_rule_with_shadow_off_is_not_shadowed(db_session):
    biz = await _business(db_session, ("content_match", {"shadow": False}))
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)

    [flag] = await detect_anomalies(r, db_session)
    assert not is_shadow(flag)


async def test_inactive_rule_does_not_run(db_session):
    biz = await _business(db_session)
    db_session.add(PolicyRule(
        tenant_id=biz.id, rule_name="off", rule_type="content_match",
        severity="high", parameters={}, is_active=False,
    ))
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    assert await detect_anomalies(r, db_session) == []


async def test_policy_validator_leaves_anomaly_rules_alone(db_session, caplog):
    biz = await _business(db_session, WEEKLY, MONTHLY, MATCH)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    with caplog.at_level(logging.WARNING):
        assert await validate_receipt(r, db_session) == []
    assert "Unknown rule type" not in caplog.text


def test_calculate_score_ignores_shadow_flags(make_flag):
    shadow = make_flag(flag_type="anomaly", severity="high", confidence=90.0)
    shadow.details = {"shadow": True}
    assert calculate_score([shadow]) == (0, "low")
    assert calculate_score([shadow, make_flag()]) == calculate_score([make_flag()])


async def test_pipeline_stores_shadow_flags_without_scoring_them(db_session):
    biz = await _business(db_session, MATCH)
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)

    flags = await run_fraud_pipeline(r, db_session)
    assert [f.flag_type for f in flags] == ["anomaly"]
    assert (r.fraud_score, r.risk_level, r.status) == (0, "low", "analyzed")
    await db_session.flush()
    assert flags[0].id is not None  # added to the session


async def test_pipeline_scores_anomaly_flags_once_out_of_shadow(db_session):
    biz = await _business(db_session, ("content_match", {"shadow": False}))
    await _receipt(db_session, biz, uuid.uuid4(), 70.85)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85)

    await run_fraud_pipeline(r, db_session)
    # ("anomaly", "medium") base 30 x 90% confidence
    assert (r.fraud_score, r.status) == (27, "flagged")


async def test_pipeline_does_not_content_match_an_image_duplicate(db_session):
    biz = await _business(db_session, MATCH)
    first = await _receipt(db_session, biz, uuid.uuid4(), 70.85, image_hash="f" * 16)
    r = await _receipt(db_session, biz, uuid.uuid4(), 70.85, image_hash="f" * 16)

    flags = await run_fraud_pipeline(r, db_session)
    assert [f.flag_type for f in flags] == ["duplicate"]
    assert flags[0].details["duplicate_receipt_id"] == first.id


async def test_explain_ignores_shadow_flags(client, db_session, user_a_id):
    user = uuid.UUID(user_a_id)
    biz = await _business(db_session)
    db_session.add(Membership(tenant_id=biz.id, user_id=user, role="member"))
    r = await _receipt(db_session, biz, user, 70.85)
    db_session.add(FraudFlag(
        receipt_id=r.id, flag_type="anomaly", severity="high",
        details={"signal": "content_match", "shadow": True}, confidence_score=Decimal("90"),
    ))
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/receipts/{r.id}/explain",
        headers={"Authorization": f"Bearer {make_token(user_a_id)}", "X-Tenant-ID": str(biz.id)},
    )
    assert resp.status_code == 400
    assert "no scored fraud flags" in resp.json()["detail"]
