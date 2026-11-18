# Company-Specific Policy Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a business's admins list, edit, switch on/off, add and delete their own fraud policy rules, from an admin-only API and Policies page.

**Architecture:** A rule-type registry validates each type's `parameters` and is the single place a new type is added. A new `/policy-rules` router (all routes `require_admin`, all scoped to `context.tenant_id`) does CRUD on the existing `policy_rules` table. A new admin-only `/policies` page calls it. The fraud pipeline already reads the business's active rules at analysis time, so it needs no change. No migration.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest-asyncio; React 19 + TS strict + Tailwind (no frontend test runner).

**Spec:** `docs/superpowers/specs/2026-11-18-policy-editing-design.md`

## Global Constraints

- Admin-only: all four endpoints use `require_admin(...)` from `app/auth.py`. Exact messages: GET → `"Only admins can view policies"`; POST/PATCH/DELETE → `"Only admins can edit policies"`.
- A rule in another business → plain 404 `"Policy rule not found"`. Unknown `rule_type` on create → 400 `"Unknown rule type '<type>'"`.
- `rule_type` can never change (PATCH with `rule_type` → 422). `severity` ∈ `low|medium|high`. `rule_name` 1–100 chars, trimmed.
- Bounds: `amount_limit.limit` > 0 and ≤ 1,000,000, optional `category` 1–50 chars lowercased; `future_date` parameters must be `{}`; `vendor_category.allowed_categories` 1+ categories, each 1+ keywords, ≤ 100 keywords per category, names/keywords trimmed + lowercased; `round_number.min_amount` > 0 and ≤ 1,000,000; `short_window_duplicate.window_hours` int 1–720 and `amount_tolerance_pct` 0–100.
- Unknown stored types are listable, can be toggled and deleted, but their `parameters` cannot be edited (400).
- Changes apply to receipts analyzed afterwards; no automatic re-analysis. Deleting a rule never touches existing fraud flags.
- No new migration, no new dependency. `app/services/policy_validator.py` and `policy_defaults.py` stay unchanged.
- Frontend: members never see the Policies link and are redirected away from `/policies`; delete requires an explicit Cancel/Delete confirmation. `npm run lint` must show no errors beyond the 5 known ones (4× set-state-in-effect in TenantProvider/ReceiptDetail/ReceiptsList, 1× react-refresh in button.tsx); don't call `setState` synchronously at the top of an effect, and keep non-component exports out of component files.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never touch the Supabase database or run alembic against it; tests use the local Postgres only.

## Review Focus

- `PATCH` that sets `parameters` to `{}` (or drops `limit`) on an `amount_limit` rule → 400 with a readable message, never a 500 (Task 2 test).
- Numbers sent as strings (`"75"`), booleans (`true` is an int in Python), negative, zero, `inf`/huge values are rejected for every numeric setting (Task 1 tests).
- A member calling all four endpoints, including with a non-existent rule id, gets the exact 403, never a 404 that reveals anything (Task 2 test).
- Another business's rule id on PATCH and DELETE → 404 and the rule is unchanged (Task 2 test).
- Switching a rule off really removes its flag on the next analysis, and deleting a rule leaves previously created flags intact (Task 2 tests).
- A member who types `/policies` directly lands on `/`, and the sidebar shows no Policies link (Task 3 verification).

---

## File Structure

- Create `backend/app/services/policy_rules.py` — registry + validators.
- Create `backend/app/schemas/policy_rule.py` — request/response models.
- Create `backend/app/api/v1/policy_rules.py` — the four endpoints; modify `backend/app/main.py` to register it.
- Tests: create `backend/tests/test_policy_rule_registry.py`, `backend/tests/test_policy_rules_api.py`.
- Frontend: modify `frontend/src/lib/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`; create `frontend/src/lib/policyRules.ts` (type labels, summaries, form↔parameters helpers), `frontend/src/pages/Policies.tsx`, `frontend/src/components/PolicyRuleForm.tsx`.

---

### Task 1: Rule-type registry and validators

**Files:**
- Create: `backend/app/services/policy_rules.py`
- Test: `backend/tests/test_policy_rule_registry.py`

**Interfaces:**
- Produces:
  - `class PolicyRuleError(ValueError)` — message is user-readable and starts with the offending setting's name.
  - `KNOWN_RULE_TYPES: frozenset[str]` = the five types in Global Constraints.
  - `def validate_parameters(rule_type: str, parameters: dict) -> dict` — returns the cleaned parameters (trimmed/lowercased/deduped keywords, numbers kept as int/float) or raises `PolicyRuleError`; unknown type → `PolicyRuleError(f"Unknown rule type '{rule_type}'")`.

- [ ] **Step 1: Write failing tests** in `test_policy_rule_registry.py` (sync tests, no DB):
  `test_amount_limit_accepts_limit_and_optional_category` (`{"limit": 75, "category": " Meals "}` → `{"limit": 75, "category": "meals"}`); `test_amount_limit_rejects_missing_zero_negative_huge_string_bool_and_inf` (parametrized over `{}`, `{"limit": 0}`, `{"limit": -1}`, `{"limit": 1_000_001}`, `{"limit": "75"}`, `{"limit": True}`, `{"limit": float("inf")}`, each raising `PolicyRuleError` whose message contains `"limit"`); `test_amount_limit_boundary_values` (`0.01` and `1_000_000` accepted); `test_future_date_requires_empty_parameters` (`{}` ok, `{"x": 1}` raises); `test_vendor_category_cleans_and_dedupes_keywords` (`{"allowed_categories": {" Meals ": [" Cafe ", "cafe", "Diner"]}}` → `{"allowed_categories": {"meals": ["cafe", "diner"]}}`); `test_vendor_category_rejects_empty_categories_empty_keyword_lists_blank_strings_and_over_100_keywords`; `test_round_number_bounds` (`min_amount` 10 ok; 0, -5, "10" rejected); `test_short_window_duplicate_bounds` (`window_hours` 1 and 720 ok, 0 and 721 and 1.5 and True rejected; `amount_tolerance_pct` 0 and 100 ok, -1 and 100.5 rejected; both keys required); `test_unknown_type_raises_unknown_rule_type_message`; `test_known_rule_types_has_the_five_types`.
- [ ] **Step 2: Run** `cd backend && python3 -m pytest tests/test_policy_rule_registry.py -q` — expect FAIL (module missing).
- [ ] **Step 3: Implement** the three symbols in `app/services/policy_rules.py`: a dict mapping type → validator function; shared helper that accepts only `int`/`float` that are not `bool` and are finite; error messages like `"limit must be greater than 0 and at most 1,000,000"`.
- [ ] **Step 4: Run** the file — PASS; then `python3 -m pytest -q` — all green.
- [ ] **Step 5: Commit** `feat: policy rule type registry with per-type validation`.

### Task 2: Policy-rule API

**Files:**
- Create: `backend/app/schemas/policy_rule.py`, `backend/app/api/v1/policy_rules.py`
- Modify: `backend/app/main.py` (import and `app.include_router(policy_rules.router, prefix="/api/v1")` beside the others)
- Test: `backend/tests/test_policy_rules_api.py`

**Interfaces:**
- Consumes: `require_admin(action)`, `RequestContext` (`app/auth.py`); `validate_parameters`, `PolicyRuleError`, `KNOWN_RULE_TYPES` (Task 1); `PolicyRule` model; `build_default_rules(tenant_id)` for test seeding.
- Produces:
  - `PolicyRuleCreate(rule_name: str, rule_type: str, severity: Literal["low","medium","high"], parameters: dict = {}, is_active: bool = True)`
  - `PolicyRuleUpdate` — all optional: `rule_name`, `severity`, `parameters`, `is_active`; `model_config = ConfigDict(extra="forbid")` so `rule_type` → 422; a body with no fields → 422.
  - `PolicyRuleResponse(id, rule_name, rule_type, severity, parameters, is_active, created_at)` with `from_attributes=True`.
  - Router `prefix="/policy-rules"`: `GET ""` → `list[PolicyRuleResponse]` ordered by id; `POST ""` → 201; `PATCH "/{rule_id}"`; `DELETE "/{rule_id}"` → 204. Every route scopes by `context.tenant_id`; a `PolicyRuleError` becomes `HTTPException(400, str(err))`.

- [ ] **Step 1: Write failing tests** in `test_policy_rules_api.py`, using fixtures `client`, `db_session`, `user_a_id`, `user_b_id`, `make_token` and a local helper that seeds a tenant with an admin (`user_a`) and a member (`user_b`) plus `build_default_rules(tenant.id)` (follow `tests/test_roles_receipts.py::_seed_team`). Tests (exact status/messages):
  - `test_admin_lists_the_business_rules` (6 defaults, ordered by id).
  - `test_member_gets_exact_403_on_all_four_endpoints` — GET → `"Only admins can view policies"`; POST/PATCH/DELETE (including a non-existent id `99999`) → `"Only admins can edit policies"`.
  - `test_admin_creates_rule_of_each_known_type` and `test_create_inactive_rule_with_is_active_false`.
  - `test_create_unknown_type_is_400_with_message`; `test_create_invalid_parameters_is_400_readable` (amount_limit without `limit`).
  - `test_patch_updates_name_severity_limit_and_toggle`.
  - `test_patch_cannot_change_rule_type_is_422`; `test_patch_empty_body_is_422`.
  - `test_patch_parameters_are_validated_against_stored_type` — `parameters: {}` on an `amount_limit` rule → 400 (never 500).
  - `test_patch_unknown_stored_type_allows_toggle_but_not_parameters` (insert a rule with `rule_type="mystery"`: `is_active=false` → 200; `parameters={"a":1}` → 400).
  - `test_other_business_rule_is_404_and_unchanged` for PATCH and DELETE.
  - `test_delete_removes_rule_and_returns_204`.
  - `test_toggle_off_removes_flag_on_next_analysis`: seed a `meals` receipt over $75 (status `"analyzed"`, a `transaction_date`, `merchant="cafe"`), `POST /receipts/{id}/analyze` as admin → has an `amount_limit` flag; `PATCH` the Meal rule `is_active=false`; analyze again → no `amount_limit` flag.
  - `test_delete_rule_keeps_existing_flags`: after analysis produced a flag, delete its rule; `GET /receipts/{id}` still returns the flag.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_policy_rules_api.py -q` — FAIL (404s for the missing router).
- [ ] **Step 3: Implement** schemas, router and registration. Follow `app/api/v1/tenants.py` style (`await db.flush()`, `Depends(get_db)`); `PATCH` validates `parameters` with the stored `rule_type` unless it is not in `KNOWN_RULE_TYPES` (then 400 `"Parameters can't be edited for unknown rule type '<type>'"`); `rule_name` is trimmed and length-checked in the schema.
- [ ] **Step 4: Run** the file — PASS; then the full suite — all green (existing tenant/receipt tests untouched).
- [ ] **Step 5: Commit** `feat: admin-only policy rule endpoints`.

### Task 3: Frontend — API client, route, nav, rule list with toggle and delete

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`
- Create: `frontend/src/lib/policyRules.ts`, `frontend/src/pages/Policies.tsx`

**Interfaces:**
- Consumes: backend endpoints (Task 2); `useRole()` from `@/auth/tenantContext`; `ApiError` messages already shown via `err.message`.
- Produces:
  - In `api.ts`: `export type RuleType = 'amount_limit' | 'future_date' | 'vendor_category' | 'round_number' | 'short_window_duplicate'`; `export interface PolicyRule { id: number; rule_name: string; rule_type: string; severity: 'low'|'medium'|'high'; parameters: Record<string, unknown>; is_active: boolean; created_at: string }`; `api.listPolicyRules(): Promise<PolicyRule[]>`, `api.createPolicyRule(body: {rule_name: string; rule_type: RuleType; severity: PolicyRule['severity']; parameters: Record<string, unknown>; is_active?: boolean}): Promise<PolicyRule>`, `api.updatePolicyRule(id: number, body: Partial<Pick<PolicyRule,'rule_name'|'severity'|'parameters'|'is_active'>>): Promise<PolicyRule>`, `api.deletePolicyRule(id: number): Promise<void>` (204 has no body — don't call `res.json()`).
  - In `lib/policyRules.ts`: `RULE_TYPE_LABELS: Record<RuleType, string>`; `summarizeRule(rule: PolicyRule): string` (e.g. `"Meals ≤ $75"`, `"Min $10"`, `"48h window, 10% tolerance"`, `"3 categories"`, `""` for future_date and unknown types).

- [ ] **Step 1: Implement** the api additions and `policyRules.ts` helpers.
- [ ] **Step 2: Implement `Policies.tsx`** (export `Policies`): loads rules on mount (no synchronous setState at the top of the effect); shows each rule's name, type label, severity badge, `summarizeRule` text, an on/off toggle (calls `updatePolicyRule(id, {is_active})`, updates the row from the response) and a Delete button that swaps to an inline "Delete '<name>'? This can't be undone." with Cancel/Delete before calling `deletePolicyRule`; Edit and "Add rule" buttons are rendered disabled placeholders until Task 4. Inline error line using `err.message`; empty state; the note "Changes apply to new receipts. Re-analyze older receipts to apply them."
- [ ] **Step 3: Route and nav.** In `App.tsx` add `<Route path="/policies" element={<AdminOnly><Policies /></AdminOnly>} />` inside the `Layout` route, where `AdminOnly` is a small local component that renders `<Navigate to="/" replace />` unless `useRole().isAdmin`. In `Layout.tsx` append `{ to: '/policies', label: 'Policies', exact: false }` to the links only when `isAdmin`.
- [ ] **Step 4: Verify** `cd frontend && npm run build` passes; `npm run lint` shows only the 5 known errors.
- [ ] **Step 5: Commit** `feat(frontend): admin Policies page with toggle and confirmed delete`.

### Task 4: Frontend — edit and add forms

**Files:**
- Create: `frontend/src/components/PolicyRuleForm.tsx`
- Modify: `frontend/src/pages/Policies.tsx`, `frontend/src/lib/policyRules.ts`

**Interfaces:**
- Consumes: Task 3's `PolicyRule`, `RuleType`, `api.createPolicyRule`/`updatePolicyRule`, `RULE_TYPE_LABELS`.
- Produces:
  - In `lib/policyRules.ts`: `defaultParameters(type: RuleType): Record<string, unknown>` (amount_limit `{limit: 100}`, future_date `{}`, vendor_category `{allowed_categories: {meals: ['restaurant']}}`, round_number `{min_amount: 10}`, short_window_duplicate `{window_hours: 48, amount_tolerance_pct: 10}`); `categoriesToText(c: Record<string, string[]>): string` (one line per category, `name: kw1, kw2`) and `textToCategories(text: string): Record<string, string[]>` (inverse; ignores blank lines).
  - `export function PolicyRuleForm(props: { mode: 'create' | 'edit'; rule?: PolicyRule; onSaved: (r: PolicyRule) => void; onCancel: () => void })` — fields: name, severity select, the type's own settings (create mode also has a type select that resets settings to `defaultParameters`); Save calls create or update and shows server error text inline; edit mode cannot change type.

- [ ] **Step 1: Implement helpers** in `policyRules.ts`.
- [ ] **Step 2: Implement `PolicyRuleForm.tsx`** with per-type inputs: amount_limit → limit + optional category; future_date → no settings; vendor_category → textarea via `categoriesToText`/`textToCategories`; round_number → min amount; short_window_duplicate → window hours + tolerance %. Numbers are sent as numbers (not strings). Unknown rule types are not editable (the Edit button is hidden for them; their toggle/delete still work).
- [ ] **Step 3: Wire into `Policies.tsx`:** "Add rule" opens the create form; "Edit" opens the edit form for that row; on save, insert/replace the row and close the form.
- [ ] **Step 4: Verify** `npm run build` passes; `npm run lint` shows only the 5 known errors.
- [ ] **Step 5: Commit** `feat(frontend): create and edit policy rules`.

### Task 5: Docs and verification (controller-gated steps marked)

**Files:** Modify `README.md` (short "Editing policies" note: admin-only Policies page, rule types, changes apply to new receipts), `TODO.md` (tick "Implement company-specific policy editing and limits" if present, else add it ticked).

- [ ] **Step 1: Update docs**, commit `docs: describe policy editing`.
- [ ] **Step 2: Full suite** `cd backend && python3 -m pytest -q` — all green; `cd ../frontend && npm run build` passes.
- [ ] **Step 3 (local end-to-end, no Supabase):** build a throwaway local database `receiptly_policy_e2e` on localhost, run `alembic upgrade head` against it with `DATABASE_URL` and `DATABASE_URL_DIRECT` both set to it (abort unless the host is localhost), start the backend with locally minted HS256 tokens and stubbed Supabase Storage/email lookup (same approach as the roles check), then as admin: create a rule, edit its limit, toggle it off, delete it; as member: all four calls return the exact 403; upload a receipt over the new limit and see the flag appear and disappear with the toggle. Drop the database and stop the server afterwards.
- [ ] **Step 4 (needs the user's yes): push the branch and open the PR against `main`.**
