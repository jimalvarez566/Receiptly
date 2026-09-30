# Tenant-Scope the Fraud Pipeline — Design

**Date:** 2026-09-10
**Branch:** `feature/supabase-multitenant-auth` (continues on the same branch)
**Status:** Approved for implementation planning

## Problem

The `main` merge brought in the MVP fraud pipeline (OCR, duplicate detection,
policy validation, scoring). The API endpoints were retrofitted with tenant
scoping during the merge, but the **fraud services themselves still query
globally**, so they leak data across businesses:

- `policy_validator.validate_receipt` runs
  `select(PolicyRule).where(PolicyRule.is_active == True)` — every business's
  policy rules are applied to every receipt.
- `duplicate_detector.detect_duplicates` scans `select(Receipt.id,
  Receipt.image_hash)` across **all** receipts — Business A gets a "duplicate"
  flag when Business B uploads an identical image, and the review endpoint's
  `original_receipt_id` can point at another tenant's receipt.
- `policy_validator._check_short_window_duplicate` queries candidate receipts by
  `employee_id` only, with no tenant filter.
- `seed_policies.py` builds `PolicyRule` objects with no `tenant_id`; the column
  is now `NOT NULL`, so the script fails.

## Goal

Every fraud-detection query is scoped to `receipt.tenant_id`. Creating a business
gives it a working set of default policy rules. No cross-tenant flag, match, or
`original_receipt_id` is ever produced.

## Non-goals

Row-Level Security, a per-tenant rule-editing API, and any change to detection
thresholds or scoring weights are out of scope (Phase 2 / unrelated).

## Components

### 1. `app/services/policy_defaults.py` (new)

Holds `DEFAULT_RULES: list[dict]` — the 6-rule list currently inline in
`seed_policies.py` (meal amount limit, supplies amount limit, no future dates,
approved expense categories, round number detection, short window duplicate).
Each dict has `rule_name`, `rule_type`, `severity`, `parameters`. This module is
the single source of truth; both `seed_policies.py` and the tenants API import
it. Moving the list is a pure relocation — no content change.

### 2. `app/services/policy_validator.py`

- `validate_receipt(receipt, db)`: the rule query becomes
  ```python
  select(PolicyRule)
      .where(PolicyRule.is_active == True)  # noqa: E712
      .where(PolicyRule.tenant_id == receipt.tenant_id)
  ```
- `_check_short_window_duplicate(rule, receipt, db)`: the candidate query gains
  `.where(Receipt.tenant_id == receipt.tenant_id)`.
- No change to the pure rule functions (`_check_amount_limit`,
  `_check_future_date`, `_check_vendor_category`, `_check_round_number`) — they
  take `(rule, receipt)` and never query.

### 3. `app/services/duplicate_detector.py`

- Change `detect_duplicates(receipt_id, image_hash, db)` →
  `detect_duplicates(receipt, db)`, consistent with `validate_receipt(receipt,
  db)`. Inside, read `receipt.id` and `receipt.image_hash` from the object.
- The hash-scan query gains `.where(Receipt.tenant_id == receipt.tenant_id)`.
- `compute_image_hash(file_path)` is unchanged.

### 4. `app/services/fraud_detection_pipeline.py`

- Update the one call site: `detect_duplicates(receipt, db)` instead of
  `detect_duplicates(receipt.id, receipt.image_hash, db)`. The
  `if receipt.image_hash:` guard stays.

### 5. `app/api/v1/tenants.py`

- `create_tenant`: after `db.add(Membership(... role="owner"))` and its
  `db.flush()`, insert one `PolicyRule(tenant_id=tenant.id, rule_name=...,
  rule_type=..., severity=..., parameters=..., is_active=True)` for every entry
  in `DEFAULT_RULES`. Import `DEFAULT_RULES` from
  `app.services.policy_defaults`.

### 6. `seed_policies.py`

- Import `DEFAULT_RULES` from `app.services.policy_defaults` (drop the inline
  copy).
- Add `argparse` with a required `--tenant-id` (int). Usage:
  `python seed_policies.py --tenant-id 3`.
- The "skip if exists" check matches on `(tenant_id, rule_name)`:
  ```python
  select(PolicyRule)
      .where(PolicyRule.tenant_id == tenant_id)
      .where(PolicyRule.rule_name == rule_def["rule_name"])
  ```
- Each inserted `PolicyRule` gets `tenant_id=tenant_id`.
- Optional nicety: if no tenant with that id exists, print an error and exit 1.

### 7. `tests/conftest.py` — factory fixtures

- `make_receipt(...)`: add a `tenant_id=1` parameter (default `1`) and set
  `r.tenant_id = tenant_id`.
- `make_rule(...)`: add a `tenant_id=1` parameter (default `1`) and set
  `rule.tenant_id = tenant_id`.

This keeps the existing `tests/test_services/` suite constructing valid objects.
Those tests call the pure rule functions and `compute_image_hash` directly, so
the default value is sufficient; none of them exercise the tenant-filtered
queries.

## Testing

### New — `tests/test_fraud_pipeline_scoping.py`

Uses the async `db_session` fixture. Seeds two tenants (A, B) with memberships.

1. **Duplicate detection is tenant-scoped.** Insert a receipt for Tenant A and
   one for Tenant B with the *same* `image_hash`. `detect_duplicates(receipt_b,
   db)` returns `[]` (A's receipt is invisible). Add a second receipt in
   Tenant B with that hash → now `detect_duplicates` for the newest B receipt
   returns one flag pointing only at the other B receipt.
2. **Policy rules are tenant-scoped.** Give Tenant A an `amount_limit` rule
   (`limit` 10). A $500 receipt in Tenant B (which has no rules) yields no
   policy flags from `validate_receipt`. The same $500 receipt in Tenant A is
   flagged.
3. **Short-window duplicate is tenant-scoped.** Same `employee_id`, same
   merchant, same amount, within the window, but different tenants → no flag.
4. **`review` cross-tenant `original_receipt_id`.** End-to-end through the API:
   Tenant A and Tenant B each upload an identical image (mock OCR/hash so both
   get the same `image_hash`), then `PATCH /api/v1/receipts/{b_id}/review` —
   the response `original_receipt_id` is `null`, not A's receipt id.

### New — `tests/test_tenants.py` addition

`test_create_tenant_seeds_default_policies`: after `POST /api/v1/tenants`,
`select(PolicyRule).where(PolicyRule.tenant_id == new_id)` returns
`len(DEFAULT_RULES)` rows, and every row's `tenant_id` is the new tenant.

### Regression

Full suite green: the 86 current tests plus the new ones. `alembic check` still
clean (no schema change in this work).

## Rollout notes

- No migration — `policy_rules.tenant_id` already exists (added in
  `a1b2c3d4e5f6`).
- For the real Supabase database (Task 11), after creating the first business
  through the API it will already have the 6 default rules; older businesses (if
  any) need `python seed_policies.py --tenant-id <id>`.
