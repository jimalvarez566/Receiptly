# Tenant-Scope the Fraud Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every fraud-detection query (policy rules, perceptual-hash duplicate scan, short-window duplicate scan) filter by `receipt.tenant_id`, and give each new business a working set of default policy rules.

**Architecture:** The fraud services already receive either a `Receipt` object or its id; the fix is adding one `.where(... == receipt.tenant_id)` clause per query and threading the `receipt` object where only the id was passed. Default policy rules are defined once in a new `policy_defaults` module and inserted both by `POST /api/v1/tenants` (auto) and by `seed_policies.py --tenant-id N` (backfill).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest + pytest-asyncio, local Postgres `fraud_detection_test`.

---

## Reference: the spec

`docs/superpowers/specs/2026-09-10-tenant-scope-fraud-pipeline-design.md` — read it before starting.

## Context the engineer needs

- Branch: `feature/supabase-multitenant-auth`. Stay on it. Never touch `main`.
- Run tests with `python3` (there is no `python` on this machine): `cd backend && python3 -m pytest`.
- Current suite: **86 passing**. It must stay green.
- `Receipt` has `tenant_id: Mapped[int]` (FK, NOT NULL) and `image_hash: Mapped[str | None]`.
- `PolicyRule` has `tenant_id: Mapped[int]` (FK, NOT NULL), `rule_name`, `rule_type`, `severity`, `parameters` (JSONB), `is_active` (default True).
- `tests/conftest.py` provides async fixtures `db_session`, `client`, `user_a_id`, `user_b_id`, `make_token`, plus sync factories `make_receipt`, `make_rule`, `make_flag`.
- The fraud services and their only callers:
  - `app/services/policy_validator.py` — `validate_receipt(receipt, db)`; only caller is `fraud_detection_pipeline.py:44`.
  - `app/services/duplicate_detector.py` — `detect_duplicates(receipt_id, image_hash, db)`; only caller is `fraud_detection_pipeline.py:35`. `compute_image_hash(file_path)` is pure and unchanged.
  - `app/services/fraud_detection_pipeline.py` — `run_fraud_pipeline(receipt, db)`.
- `tests/test_services/test_policy_validator.py` calls only the pure functions `_check_amount_limit/_check_future_date/_check_vendor_category/_check_round_number(rule, receipt)`. `tests/test_services/test_duplicate_detector.py` calls only `compute_image_hash`. Neither touches the DB-querying functions, so adding a `tenant_id` default to the factories is safe and additive.

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `backend/app/services/policy_defaults.py` | `DEFAULT_RULES` (the 6 default rule dicts) + `build_default_rules(tenant_id) -> list[PolicyRule]` |
| `backend/tests/test_policy_defaults.py` | shape of `DEFAULT_RULES`, behavior of `build_default_rules` |
| `backend/tests/test_fraud_pipeline_scoping.py` | cross-tenant isolation for `validate_receipt`, `_check_short_window_duplicate`, `detect_duplicates` |

### Modified files

| Path | Change |
|---|---|
| `backend/app/services/policy_validator.py` | `validate_receipt` rule query + `_check_short_window_duplicate` candidate query gain a `tenant_id` filter |
| `backend/app/services/duplicate_detector.py` | `detect_duplicates` takes `receipt` instead of `(receipt_id, image_hash)`; hash-scan query gains a `tenant_id` filter |
| `backend/app/services/fraud_detection_pipeline.py` | update the `detect_duplicates` call site |
| `backend/app/api/v1/tenants.py` | `create_tenant` inserts `build_default_rules(tenant.id)` after the owner membership |
| `backend/seed_policies.py` | import `DEFAULT_RULES` from `policy_defaults`; require `--tenant-id`; skip check is per `(tenant_id, rule_name)` |
| `backend/tests/conftest.py` | `make_receipt` / `make_rule` gain a `tenant_id=1` parameter |
| `backend/tests/test_tenants.py` | add `test_create_tenant_seeds_default_policies` |
| `backend/tests/test_receipts_scoping.py` | add `test_review_original_receipt_id_is_tenant_scoped` |

No migration — `policy_rules.tenant_id` already exists.

---

## Task 1: `policy_defaults` module

**Files:**
- Create: `backend/app/services/policy_defaults.py`
- Test: `backend/tests/test_policy_defaults.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_policy_defaults.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_policy_defaults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.policy_defaults'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/policy_defaults.py`:

```python
"""Default policy rules seeded for every business.

Single source of truth for both the tenants API (auto-seed on business
creation) and seed_policies.py (backfill for pre-existing businesses).
"""
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
            parameters=d["parameters"],
            is_active=True,
        )
        for d in DEFAULT_RULES
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_policy_defaults.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/policy_defaults.py tests/test_policy_defaults.py
git commit -m "feat: add policy_defaults module (DEFAULT_RULES + build_default_rules)"
```

---

## Task 2: Tenant-scope `policy_validator`

**Files:**
- Modify: `backend/app/services/policy_validator.py`
- Test: `backend/tests/test_fraud_pipeline_scoping.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_fraud_pipeline_scoping.py`:

```python
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Membership, Tenant
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt
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

    shared_employee = 7
    now = datetime.now(timezone.utc)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_fraud_pipeline_scoping.py -v`
Expected: FAIL — `test_policy_rules_do_not_cross_tenants` fails because `flags_b` is non-empty (A's rule leaks).

- [ ] **Step 3: Add the tenant filter**

In `backend/app/services/policy_validator.py`, in `validate_receipt`, change:

```python
    result = await db.execute(
        select(PolicyRule).where(PolicyRule.is_active == True)  # noqa: E712
    )
```

to:

```python
    result = await db.execute(
        select(PolicyRule)
        .where(PolicyRule.is_active == True)  # noqa: E712
        .where(PolicyRule.tenant_id == receipt.tenant_id)
    )
```

In the same file, in `_check_short_window_duplicate`, change the candidate query:

```python
    result = await db.execute(
        select(Receipt.id, Receipt.merchant, Receipt.amount, Receipt.created_at)
        .where(Receipt.employee_id == receipt.employee_id)
        .where(Receipt.merchant.is_not(None))
        .where(Receipt.amount.is_not(None))
        .where(Receipt.id != receipt.id)
        .where(Receipt.created_at >= cutoff)
    )
```

to add one line:

```python
    result = await db.execute(
        select(Receipt.id, Receipt.merchant, Receipt.amount, Receipt.created_at)
        .where(Receipt.tenant_id == receipt.tenant_id)
        .where(Receipt.employee_id == receipt.employee_id)
        .where(Receipt.merchant.is_not(None))
        .where(Receipt.amount.is_not(None))
        .where(Receipt.id != receipt.id)
        .where(Receipt.created_at >= cutoff)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_fraud_pipeline_scoping.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 88 passed (86 + 2). No regressions.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/services/policy_validator.py tests/test_fraud_pipeline_scoping.py
git commit -m "fix: scope policy validation and short-window duplicate check by tenant"
```

---

## Task 3: Tenant-scope `duplicate_detector` + update the pipeline

**Files:**
- Modify: `backend/app/services/duplicate_detector.py`
- Modify: `backend/app/services/fraud_detection_pipeline.py`
- Test: `backend/tests/test_fraud_pipeline_scoping.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_fraud_pipeline_scoping.py`:

```python
from app.services.duplicate_detector import detect_duplicates

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_fraud_pipeline_scoping.py -v`
Expected: FAIL — `detect_duplicates(r_b, db_session)` raises `TypeError` (signature is still `(receipt_id, image_hash, db)`).

- [ ] **Step 3: Change the signature and add the tenant filter**

In `backend/app/services/duplicate_detector.py`, replace the `detect_duplicates` function definition and its query. Change the signature line:

```python
async def detect_duplicates(
    receipt_id: int,
    image_hash: str,
    db: AsyncSession,
) -> list[FraudFlag]:
```

to:

```python
async def detect_duplicates(
    receipt: "Receipt",
    db: AsyncSession,
) -> list[FraudFlag]:
```

Immediately inside the function body (before the docstring's code, i.e. right after the docstring), add:

```python
    receipt_id = receipt.id
    image_hash = receipt.image_hash
    if image_hash is None:
        return []
```

Change the query from:

```python
    result = await db.execute(
        select(Receipt.id, Receipt.image_hash)
        .where(Receipt.image_hash.is_not(None))
        .where(Receipt.id != receipt_id)
    )
```

to:

```python
    result = await db.execute(
        select(Receipt.id, Receipt.image_hash)
        .where(Receipt.tenant_id == receipt.tenant_id)
        .where(Receipt.image_hash.is_not(None))
        .where(Receipt.id != receipt_id)
    )
```

The rest of the function body (the `for existing_id, stored_hash_str in rows:` loop) is unchanged — it already uses the local `receipt_id` and `image_hash` names.

- [ ] **Step 4: Update the only caller**

In `backend/app/services/fraud_detection_pipeline.py`, change line ~35:

```python
        duplicate_flags = await detect_duplicates(receipt.id, receipt.image_hash, db)
```

to:

```python
        duplicate_flags = await detect_duplicates(receipt, db)
```

The `if receipt.image_hash:` guard on the line above stays as-is.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_fraud_pipeline_scoping.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 90 passed (88 + 2). In particular `tests/test_receipts_scoping.py` still passes — its `_mock_upload_deps` fixture stubs `run_fraud_pipeline`, so the changed `detect_duplicates` is not exercised there.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/services/duplicate_detector.py app/services/fraud_detection_pipeline.py tests/test_fraud_pipeline_scoping.py
git commit -m "fix: scope perceptual-hash duplicate detection by tenant"
```

---

## Task 4: Auto-seed default policy rules on business creation

**Files:**
- Modify: `backend/app/api/v1/tenants.py`
- Test: `backend/tests/test_tenants.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tenants.py`:

```python
from app.models.policy_rule import PolicyRule
from app.services.policy_defaults import DEFAULT_RULES


async def test_create_tenant_seeds_default_policies(client, db_session, user_a_id):
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/tenants", json={"name": "Acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(PolicyRule).where(PolicyRule.tenant_id == tid)
    )).scalars().all()
    assert len(rows) == len(DEFAULT_RULES)
    assert all(r.tenant_id == tid for r in rows)
    assert all(r.is_active is True for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_tenants.py::test_create_tenant_seeds_default_policies -v`
Expected: FAIL — `assert 0 == 6` (no rules seeded).

- [ ] **Step 3: Seed rules in `create_tenant`**

In `backend/app/api/v1/tenants.py`, add the import near the other `from app.services...` / `from app.models...` imports:

```python
from app.services.policy_defaults import build_default_rules
```

In `create_tenant`, change:

```python
    tenant = Tenant(name=body.name, created_by_user_id=user.user_id)
    db.add(tenant)
    await db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.user_id, role="owner"))
    await db.flush()
    return tenant
```

to:

```python
    tenant = Tenant(name=body.name, created_by_user_id=user.user_id)
    db.add(tenant)
    await db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.user_id, role="owner"))
    db.add_all(build_default_rules(tenant.id))
    await db.flush()
    return tenant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_tenants.py -v`
Expected: PASS — the new test plus all existing `test_tenants.py` tests (the earlier `test_create_tenant_makes_owner_membership` only counts `Membership` rows, so it is unaffected).

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 91 passed (90 + 1).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/api/v1/tenants.py tests/test_tenants.py
git commit -m "feat: seed default policy rules when a business is created"
```

---

## Task 5: `seed_policies.py` becomes a per-tenant backfill tool

**Files:**
- Modify: `backend/seed_policies.py`
- Test: `backend/tests/test_seed_policies.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_seed_policies.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_seed_policies.py -v`
Expected: FAIL — `AttributeError: module 'seed_policies' has no attribute 'parse_args'`.

- [ ] **Step 3: Rewrite `seed_policies.py`**

Replace the entire contents of `backend/seed_policies.py` with:

```python
"""Seed the default policy rules for one business (tenant).

Run from the backend/ directory:
    python seed_policies.py --tenant-id 3

Safe to re-run — rules already present for that tenant (matched by name)
are skipped, not duplicated. New businesses created through
POST /api/v1/tenants are seeded automatically; use this only to backfill
businesses that predate that behavior.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.database import async_session
from app.models.policy_rule import PolicyRule
from app.models.tenant import Tenant
from app.services.policy_defaults import DEFAULT_RULES, build_default_rules


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed default policy rules for a tenant.")
    parser.add_argument("--tenant-id", type=int, required=True, help="Target tenant id")
    return parser.parse_args(argv)


async def seed_for_tenant(tenant_id: int) -> tuple[int, int]:
    """Insert any missing default rules for the tenant. Returns (inserted, skipped)."""
    inserted = 0
    skipped = 0

    async with async_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"No tenant with id {tenant_id}")

        existing_names = set(
            (
                await db.execute(
                    select(PolicyRule.rule_name).where(PolicyRule.tenant_id == tenant_id)
                )
            ).scalars().all()
        )

        for rule in build_default_rules(tenant_id):
            if rule.rule_name in existing_names:
                print(f"  SKIP   '{rule.rule_name}' (already exists for tenant {tenant_id})")
                skipped += 1
            else:
                db.add(rule)
                await db.flush()
                print(f"  INSERT '{rule.rule_name}' (id={rule.id})")
                inserted += 1

        await db.commit()

    return inserted, skipped


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    print(f"Seeding policy rules for tenant {ns.tenant_id}...\n")
    inserted, skipped = asyncio.run(seed_for_tenant(ns.tenant_id))
    print(f"\nDone — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_seed_policies.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Manual end-to-end check against the local test DB**

The pytest fixtures leave `fraud_detection_test` with tables created; seed a row and run the script:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test \
python3 -c "
import asyncio
from app.database import async_session
from app.models.tenant import Tenant
async def go():
    async with async_session() as db:
        t = Tenant(name='seedtest', created_by_user_id='00000000-0000-0000-0000-000000000001')
        db.add(t); await db.flush(); print('tenant', t.id); await db.commit()
asyncio.run(go())
"
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test \
python3 seed_policies.py --tenant-id 1
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test \
python3 seed_policies.py --tenant-id 1   # second run: all SKIP
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test \
python3 seed_policies.py --tenant-id 999  # expect: "No tenant with id 999", exit 1
```
Expected: first run prints 6 `INSERT`, second run prints 6 `SKIP`, the id-999 run errors and exits non-zero. (If the tenant id printed by the first snippet is not `1`, use that id in the following commands.) The `fraud_detection_test` DB is dropped/recreated by the pytest fixtures on the next run, so this leaves no lasting state.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 94 passed (91 + 3).

- [ ] **Step 7: Commit**

```bash
cd backend && git add seed_policies.py tests/test_seed_policies.py
git commit -m "feat: seed_policies.py seeds one tenant via --tenant-id, shares DEFAULT_RULES"
```

---

## Task 6: Give the sync test factories a `tenant_id`

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Update `make_receipt`**

In `backend/tests/conftest.py`, in the `make_receipt` fixture's inner `_make`, add a `tenant_id=1` parameter and set it on the object:

```python
@pytest.fixture
def make_receipt():
    """Factory for Receipt ORM instances without a DB session."""
    def _make(
        id=1,
        merchant="Starbucks",
        amount=Decimal("15.47"),
        transaction_date=date(2024, 1, 15),
        category=None,
        employee_id=None,
        image_hash=None,
        status="analyzed",
        tenant_id=1,
    ):
        r = Receipt()
        r.id = id
        r.merchant = merchant
        r.amount = amount
        r.transaction_date = transaction_date
        r.category = category
        r.employee_id = employee_id
        r.image_hash = image_hash
        r.status = status
        r.tenant_id = tenant_id
        return r
    return _make
```

- [ ] **Step 2: Update `make_rule`**

In the same file, `make_rule`'s inner `_make`:

```python
@pytest.fixture
def make_rule():
    """Factory for PolicyRule ORM instances."""
    def _make(rule_type, parameters, severity="medium", rule_name=None, id=1, tenant_id=1):
        rule = PolicyRule()
        rule.id = id
        rule.rule_name = rule_name or f"Test {rule_type} rule"
        rule.rule_type = rule_type
        rule.parameters = parameters
        rule.severity = severity
        rule.is_active = True
        rule.tenant_id = tenant_id
        return rule
    return _make
```

- [ ] **Step 3: Run the service suite**

Run: `cd backend && python3 -m pytest tests/test_services/ -v`
Expected: all pass unchanged (these tests never read `tenant_id`, but the objects are now fully-formed).

- [ ] **Step 4: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 94 passed (no change in count).

- [ ] **Step 5: Commit**

```bash
cd backend && git add tests/conftest.py
git commit -m "test: give make_receipt / make_rule factories a tenant_id default"
```

---

## Task 7: Cross-tenant `review` — `original_receipt_id` must not leak (regression test)

The `review_receipt` duplicate-chain query **already** carries
`.where(Receipt.tenant_id == context.tenant_id)` (added during the `main` merge,
commit `7c8d0dd`). This task adds the regression test that locks that behavior
in. Expect the test to pass as soon as it is written — no production code change.

**Files:**
- Test: `backend/tests/test_receipts_scoping.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_receipts_scoping.py` (the file already imports `io`, `uuid`, `pytest`, `supabase_client`, `receipts_module`, `Membership`, `Receipt`, `Tenant`, `OCRResult`, `make_token`, and defines `_seed`):

```python
from app.models.fraud_flag import FraudFlag

_SHARED_HASH = "a" * 16


async def test_review_original_receipt_id_is_tenant_scoped(client, db_session, user_a_id, user_b_id):
    ace = await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    bee = await _seed(db_session, uuid.UUID(user_b_id), "BeeCo")

    # Tenant A already has a receipt with the shared image hash.
    a_receipt = Receipt(
        image_path="a/x.png", tenant_id=ace.id,
        submitted_by_user_id=uuid.UUID(user_a_id),
        status="analyzed", image_hash=_SHARED_HASH,
    )
    db_session.add(a_receipt)
    await db_session.flush()

    # Tenant B's receipt: same hash, already analyzed, carries a duplicate flag.
    b_receipt = Receipt(
        image_path="b/x.png", tenant_id=bee.id,
        submitted_by_user_id=uuid.UUID(user_b_id),
        status="flagged", image_hash=_SHARED_HASH,
    )
    db_session.add(b_receipt)
    await db_session.flush()
    db_session.add(FraudFlag(
        receipt_id=b_receipt.id, flag_type="duplicate", severity="high",
        description="dup", details={}, confidence_score=100,
    ))
    await db_session.commit()

    token = make_token(user_b_id)
    resp = await client.request(
        "PATCH",
        f"/api/v1/receipts/{b_receipt.id}/review",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(bee.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["original_receipt_id"] is None  # A's receipt must be invisible
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `cd backend && python3 -m pytest tests/test_receipts_scoping.py -v`
Expected: PASS — the new test plus the existing four. The query is already
tenant-scoped, so this is a lock-in regression test, not a red→green cycle.

Sanity check that the guard is actually present (not a false pass): run
`grep -n "Receipt.tenant_id == context.tenant_id" backend/app/api/v1/receipts.py`
and confirm it appears inside `review_receipt`'s `earliest = await db.execute(` block.

- [ ] **Step 3: Run the full suite**

Run: `cd backend && python3 -m pytest -q`
Expected: 95 passed (94 + 1).

- [ ] **Step 4: Commit**

```bash
cd backend && git add tests/test_receipts_scoping.py
git commit -m "test: lock in that review original_receipt_id is tenant-scoped"
```

---

## Task 8: Update docs

**Files:**
- Modify: `README.md` (repo root — the only README that mentions `seed_policies`; lines 43 and 132)
- Modify: `TODO.md`

- [ ] **Step 1: Update `README.md` line ~43**

Change:

```
- Seed default rules with `python seed_policies.py`
```

to:

```
- Businesses created via `POST /api/v1/tenants` get the six default policy rules
  automatically; use `python seed_policies.py --tenant-id <id>` only to backfill a
  business created before that behavior existed.
```

- [ ] **Step 2: Update `README.md` line ~132 (the setup code block)**

In the `## Local Setup` backend code block, change the line:

```
python seed_policies.py
```

to:

```
# (optional) backfill default policy rules for a pre-existing business:
# python seed_policies.py --tenant-id <id>
```

Adjust the surrounding numbered-step comment if the step is now optional (e.g.
`# 5. (optional) Seed default policy rules for an existing business`).

- [ ] **Step 3: Update `TODO.md`**

In `TODO.md`, under the `### Supabase Auth & Multi-Tenancy` done-list block added by the merge, add:

```markdown
- [x] Fraud pipeline scoped by tenant (policy rules, pHash + short-window duplicate scans, review duplicate-chain)
- [x] Default policy rules auto-seeded on business creation; `seed_policies.py --tenant-id` for backfill
```

- [ ] **Step 4: Run the full suite once more**

Run: `cd backend && python3 -m pytest -q`
Expected: 95 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/syonchau/ai_fraud_payroll && git add README.md TODO.md
git commit -m "docs: note per-tenant policy seeding and fraud-pipeline scoping"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `policy_defaults.py` new module (`DEFAULT_RULES` + `build_default_rules`) | Task 1 |
| `validate_receipt` rule query tenant filter | Task 2 |
| `_check_short_window_duplicate` candidate query tenant filter | Task 2 |
| `detect_duplicates(receipt, db)` signature change + tenant filter | Task 3 |
| `fraud_detection_pipeline` call-site update | Task 3 |
| `create_tenant` auto-seeds `build_default_rules(tenant.id)` | Task 4 |
| `seed_policies.py` — import shared list, require `--tenant-id`, per-`(tenant_id, rule_name)` skip | Task 5 |
| `conftest.py` factories gain `tenant_id` | Task 6 |
| `tests/test_fraud_pipeline_scoping.py` — dup, policy, short-window isolation | Tasks 2, 3 |
| `test_tenants.py` — `test_create_tenant_seeds_default_policies` | Task 4 |
| `test_receipts_scoping.py` — cross-tenant `review` `original_receipt_id` | Task 7 |
| Regression: full suite green, `alembic check` clean (no schema change) | every task runs `pytest -q`; no migration touched |
| Rollout notes (README/TODO) | Task 8 |

No gaps.

**Placeholder scan:** No "TBD" / "handle edge cases" / "similar to Task N". Every code step shows complete code; every run step gives the command and the expected result.

**Type consistency:**
- `build_default_rules(tenant_id: int) -> list[PolicyRule]` — defined Task 1, called in Task 4 (`db.add_all(build_default_rules(tenant.id))`) and Task 5 (`build_default_rules(tenant_id)`).
- `DEFAULT_RULES` — defined Task 1, imported in Tasks 4, 5 and asserted in Task 1's own test.
- `detect_duplicates(receipt, db)` — new signature defined Task 3, caller updated in the same task; no other caller exists (verified: only `fraud_detection_pipeline.py:35`).
- `validate_receipt(receipt, db)` — signature unchanged; only the internal query changes (Task 2).
- `seed_policies.parse_args(argv)` / `seed_for_tenant(tenant_id)` / `main(argv)` — defined Task 5, exercised by Task 5's test.
- `_seed` / `_tenant` / `_receipt` test helpers — `_seed` is pre-existing in `test_receipts_scoping.py` (used Task 7); `_tenant` / `_receipt` are defined at the top of the new `test_fraud_pipeline_scoping.py` in Task 2 and reused in Task 3.
