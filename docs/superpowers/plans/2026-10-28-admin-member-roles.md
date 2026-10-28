# Admin vs Member Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `admin` / `member` roles so members only upload and see their own receipts, admins manage policies, members and reviews, and every denial shows a readable message.

**Architecture:** Role lives on `memberships.role` and is already on `RequestContext.role`. A `require_admin(action)` dependency factory and a `can_view_receipt` helper in `app/auth.py` centralise every permission decision (so a later "tagged attendees" feature extends one function). Receipt responses go through one presenter that redacts cross-member references for members. The frontend surfaces the backend `detail` string and hides admin-only controls.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, pytest-asyncio; React 19 + TS strict + Tailwind (no frontend test runner).

**Spec:** `docs/superpowers/specs/2026-10-28-admin-member-roles-design.md`

## Precondition

The new migration's `down_revision` is `b2c3d4e5f6a7` (RLS, PR #3: https://github.com/jimalvarez566/Receiptly/pull/3). This branch was cut from `main`, which lacks it. Before Task 1, either PR #3 is merged and this branch is rebased on `main`, or this branch is rebased onto `feature/enable-rls`. `alembic heads` must show exactly `b2c3d4e5f6a7` before starting.

## Global Constraints

- Roles are exactly `"admin"` and `"member"`; constants `ROLE_ADMIN`, `ROLE_MEMBER` in `app/auth.py`. No `owner` anywhere after Task 1.
- Denial messages (exact): `"You can't view this receipt"`, `"Only admins can approve or reject receipts"`, `"Only admins can re-run fraud analysis"`, `"Only admins can manage members"`, `"A business needs at least one admin"` (400).
- Cross-business receipt → plain 404 `"Receipt not found"`. Same-business, not visible to a member → 403.
- Duplicate detection stays business-wide; do not touch `duplicate_detector.py`.
- Never mutate ORM `FraudFlag.details` for redaction (it would persist); redact on the response copy only.
- Every new table/migration must keep `tests/test_rls_coverage.py` passing (this plan adds no tables).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A member's paginated list must report `total` for **their own** receipts only (no count leak of the business's volume).
- A member requesting `/explain` on someone else's receipt is rejected **before** any Gemini call/quota use.
- A member holding a duplicate flag pointing at a colleague's receipt: `duplicate_receipt_id` is removed from the response but the flag, severity and description remain; a pointer to their own receipt is kept.
- Two admins demoting/removing each other concurrently must not leave a business with zero admins (lock the membership rows).
- An admin demoting or removing **themselves** is allowed when another admin exists, rejected when they are the last.
- A removed user immediately loses access (existing 403 "Not a member of the requested business") while their receipts stay visible to admins.

---

## File Structure

- Modify `backend/app/auth.py` — role constants, `require_admin`, `can_view_receipt`.
- Create `backend/app/services/receipt_presenter.py` — `present_receipts` (redaction + schema conversion).
- Modify `backend/app/api/v1/receipts.py` — visibility, admin gates, presenter.
- Modify `backend/app/api/v1/tenants.py`, `backend/app/schemas/tenant.py` — admin gates, last-admin guard, role-change endpoint.
- Modify `backend/app/models/membership.py`; create `backend/alembic/versions/c3d4e5f6a7b8_admin_member_roles.py`.
- Tests: create `backend/tests/test_roles_permissions.py`, `test_roles_receipts.py`, `test_roles_members.py`; update existing tests that use `"owner"`.
- Frontend: modify `lib/api.ts`, `auth/tenantContext.ts`, `auth/TenantProvider.tsx` (no change if `useRole` derives from context), `pages/ReceiptDetail.tsx`, `components/Layout.tsx`.

---

### Task 1: Role values, model constraint, migration

**Files:**
- Modify: `backend/app/models/membership.py:17`, `backend/app/api/v1/tenants.py:31`
- Create: `backend/alembic/versions/c3d4e5f6a7b8_admin_member_roles.py`
- Modify (owner→admin): `tests/test_auth.py`, `test_auth_me.py`, `test_tenant_schemas.py`, `test_fraud_pipeline_scoping.py`, `test_models.py`, `test_receipts_scoping.py`, `test_tenants.py`

**Interfaces:**
- Produces: `Membership.role` default `"member"`; DB `CHECK (role IN ('admin','member'))` named `ck_memberships_role`; creator of a tenant is `"admin"`.

- [ ] **Step 1: Write failing tests.** In `tests/test_models.py` add `test_membership_rejects_unknown_role` (insert `role="owner"`, expect `sqlalchemy.exc.IntegrityError` on flush) and `test_membership_default_role_is_member`. In `tests/test_tenants.py` rename `test_create_tenant_makes_owner_membership` → `..._makes_admin_membership` asserting `role == "admin"`.
- [ ] **Step 2: Run** `cd backend && python3 -m pytest tests/test_models.py tests/test_tenants.py -q` — expect the three new/changed tests FAIL.
- [ ] **Step 3: Implement.** Add `CheckConstraint("role IN ('admin','member')", name="ck_memberships_role")` to `Membership.__table_args__`, default `"member"`; `create_tenant` inserts `role="admin"`. Replace every `role="owner"` / `"owner"` assertion in the listed test files with `"admin"` (`test_auth_me` expected set becomes `{("AceCo","admin"),("BeeCo","member")}`).
- [ ] **Step 4: Write migration** `c3d4e5f6a7b8` (down_revision `b2c3d4e5f6a7`): `upgrade` = `UPDATE memberships SET role='admin' WHERE role='owner'` then `op.create_check_constraint("ck_memberships_role","memberships","role IN ('admin','member')")`; `downgrade` = drop constraint, then `UPDATE memberships SET role='owner' WHERE role='admin'`.
- [ ] **Step 5: Verify migration on a scratch DB.** Using `TEST_DATABASE_URL` (a `*_test` database), run `alembic upgrade b2c3d4e5f6a7`, insert a tenant + a membership with role `owner`, run `alembic upgrade head`, assert the row's role is `admin` and inserting `role='owner'` fails; run `alembic downgrade -1`, assert it is `owner` again. Then `python3 -m pytest -q` — all green (including `test_rls_coverage`).
- [ ] **Step 6: Commit** `feat: admin/member role values with check constraint`.

### Task 2: Permission primitives

**Files:**
- Modify: `backend/app/auth.py`
- Test: `backend/tests/test_roles_permissions.py`

**Interfaces:**
- Produces:
  - `ROLE_ADMIN = "admin"`, `ROLE_MEMBER = "member"`
  - `def require_admin(action: str) -> Callable[..., Awaitable[RequestContext]]` — returns a dependency that depends on `get_current_context`; raises `HTTPException(403, f"Only admins can {action}")` for non-admins, else returns the context.
  - `def can_view_receipt(context: RequestContext, receipt: Receipt) -> bool` — admins: `True`; members: `receipt.submitted_by_user_id == context.user_id`. (Caller has already confirmed the tenant matches.)

- [ ] **Step 1: Write failing tests** in `test_roles_permissions.py` using a probe FastAPI app (pattern from `tests/test_auth_es256.py`) with a route `Depends(require_admin("do the thing"))`, overriding `get_current_context` to return an admin/member `RequestContext`: admin → 200; member → 403 with `detail == "Only admins can do the thing"`. Add pure tests for `can_view_receipt` (admin sees any; member sees own `submitted_by_user_id`; member does not see another's) using `make_receipt`-style `Receipt()` objects.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_roles_permissions.py -q` — FAIL (names undefined).
- [ ] **Step 3: Implement** the three symbols in `app/auth.py` (import `Receipt` lazily or under `TYPE_CHECKING` to avoid a circular import).
- [ ] **Step 4: Run** the file — PASS. **Step 5: Commit** `feat: require_admin dependency and can_view_receipt helper`.

### Task 3: Receipt endpoints — scoping, gates, presenter

**Files:**
- Create: `backend/app/services/receipt_presenter.py`
- Modify: `backend/app/api/v1/receipts.py` (upload response, `analyze`, `explain`, `review`, `get_receipt`, `list_receipts`)
- Test: `backend/tests/test_roles_receipts.py` (reuse the autouse stubs and `_seed`/`_png` pattern from `tests/test_receipts_scoping.py`; add a helper that seeds a tenant with an `admin` and a `member`)

**Interfaces:**
- Consumes: `require_admin`, `can_view_receipt` (Task 2).
- Produces: `async def present_receipts(receipts: list[Receipt], context: RequestContext, db: AsyncSession) -> list[ReceiptResponse]` — admins: plain `ReceiptResponse.model_validate`; members: for every flag whose `details` has `duplicate_receipt_id`, look up (one query for all ids) which of those receipts in the tenant were submitted by `context.user_id`, and drop the key from the response copy of the others. Never touches ORM `details`.

- [ ] **Step 1: Write failing tests** (each uses exact status and, for 403, exact `detail`):
  - `test_member_list_shows_only_own_and_total_is_own_count`
  - `test_admin_list_shows_all_in_business`
  - `test_member_get_own_receipt_ok`; `test_member_get_colleague_receipt_is_403_cant_view` (`"You can't view this receipt"`); `test_get_other_business_receipt_is_404_for_member_and_admin`
  - `test_member_cannot_review` (`"Only admins can approve or reject receipts"`), `test_member_cannot_analyze` (`"Only admins can re-run fraud analysis"`), `test_admin_can_review_and_analyze`
  - `test_member_explain_colleague_receipt_403_before_gemini` (monkeypatch `receipts_module.call_gemini` to a function that fails the test if called)
  - `test_member_duplicate_flag_details_redacted_for_colleague_receipt`, `test_member_duplicate_flag_keeps_pointer_to_own_receipt`, and `test_redaction_does_not_modify_database_row` (re-read the flag via `db_session` after the request; `duplicate_receipt_id` still present)
  - `test_member_upload_still_works_and_response_is_present_redacted`
- [ ] **Step 2: Run** `python3 -m pytest tests/test_roles_receipts.py -q` — FAIL.
- [ ] **Step 3: Implement.** Add a private helper in `receipts.py`: `async def _get_receipt_for(context, receipt_id, db) -> Receipt` — loads with flags filtered by `tenant_id`; 404 `"Receipt not found"` if absent; 403 `"You can't view this receipt"` if `not can_view_receipt`. Use it in `get_receipt` and `explain_receipt` (before the Gemini branch). `analyze_receipt` and `review_receipt` gain `context: RequestContext = Depends(require_admin("re-run fraud analysis"))` / `("approve or reject receipts")` in place of `get_current_context`. `list_receipts` adds `Receipt.submitted_by_user_id == context.user_id` to both the select and the count when `context.role != ROLE_ADMIN`. `get`, `list` and `upload` return via `present_receipts` (list endpoint builds the dict with converted receipts); `review` keeps its existing `model_validate(...).model_dump()` path (admins only).
- [ ] **Step 4: Run** the new file, then `python3 -m pytest -q` — all green. **Step 5: Commit** `feat: scope receipts by role and gate admin actions`.

### Task 4: Member management

**Files:**
- Modify: `backend/app/api/v1/tenants.py`, `backend/app/schemas/tenant.py`
- Test: `backend/tests/test_roles_members.py`

**Interfaces:**
- Consumes: `require_admin` (Task 2).
- Produces: `class MemberRoleUpdate(BaseModel): role: Literal["admin","member"]`; `PATCH /api/v1/tenants/{tenant_id}/members/{user_id}` → `MemberResponse`; helper `async def _count_admins_locked(db, tenant_id) -> int` that selects the tenant's admin membership rows `with_for_update()` and returns the count.

- [ ] **Step 1: Write failing tests:** member `POST`/`DELETE`/`PATCH` members → 403 `"Only admins can manage members"`; member `GET` members → 200; admin adds a member (stub `supabase_client.lookup_user_id_by_email`) → role `member`; admin promotes member → `admin`; admin removes a member → 204; sole admin removing self → 400 `"A business needs at least one admin"`; sole admin demoting self → same 400; two admins: one demotes self → 200; removing a member from another business's path with mismatching `X-Tenant-ID` still 403 (existing behavior); `PATCH` unknown user → 404 `"Membership not found"`.
- [ ] **Step 2: Run** the file — FAIL.
- [ ] **Step 3: Implement.** Replace `get_current_context` with `require_admin("manage members")` on `add_member`, `remove_member`, and the new `PATCH` (list stays on `get_current_context`). In remove/patch, if the target is currently an admin and the action would remove admin status, call `_count_admins_locked` and reject with 400 when the count is 1.
- [ ] **Step 4: Run** the file, then the full suite — green. **Step 5: Commit** `feat: admin-only member management with last-admin guard`.

### Task 5: Frontend — readable errors and role-aware UI

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/auth/tenantContext.ts`, `frontend/src/pages/ReceiptDetail.tsx`, `frontend/src/components/Layout.tsx`

**Interfaces:**
- Produces: `class ApiError extends Error { status: number }` whose `message` is the backend `detail` string (fallback `"Request failed (<status>)"`, for validation arrays join `msg`s); `Membership.role: 'admin' | 'member'`; `api.updateMemberRole(tenantId, userId, role)`; `useRole(): { role: 'admin'|'member'|null; isAdmin: boolean }` exported from `tenantContext.ts`, derived from the active membership.

- [ ] **Step 1: Implement `ApiError` parsing** in `api.ts`: one shared `async function toApiError(res: Response): Promise<ApiError>` used by `request()` and `uploadReceipt` (replacing `` `${res.status}: ${text}` ``). Keep the 401 sign-out path first. Pages already render `err.message`, so no per-page change is needed for the message text.
- [ ] **Step 2: Implement `useRole`** and add `updateMemberRole` (`PATCH` the new endpoint).
- [ ] **Step 3: Gate controls** in `ReceiptDetail.tsx`: render Approve/Reject (and the `|` separator) and Re-analyze only when `isAdmin`. Members still see the explain button.
- [ ] **Step 4: Show the role** in `Layout.tsx` as a small muted label (`Admin`/`Member`) under the business `<select>`.
- [ ] **Step 5: Verify:** `cd frontend && npm run build` passes; `npm run lint` reports no errors beyond the 5 pre-existing ones (set-state-in-effect ×4, react-refresh in button.tsx).
- [ ] **Step 6: Commit** `feat(frontend): show backend error messages and gate admin controls`.

### Task 6: Docs, manual verification, apply

**Files:** Modify `README.md` (the "you become its owner" line → admin; document roles and the matrix), `TODO.md` (tick roles).

- [ ] **Step 1: Update docs**, commit `docs: describe admin/member roles`.
- [ ] **Step 2: Full suite** `cd backend && python3 -m pytest -q` — all green.
- [ ] **Step 3 (needs user's yes before running): apply** `alembic upgrade head` to the live database; confirm `select role, count(*) from memberships group by 1` shows no `owner`.
- [ ] **Step 4: Manual check** (backend + frontend running): as the existing admin account open Northwind; add a second signed-up account as `member` (or use the `PATCH` endpoint); as the member confirm only own receipts show, a direct URL to another's receipt shows "You can't view this receipt", and no Approve/Reject/Re-analyze buttons; as admin confirm all of it works. Record results.
- [ ] **Step 5: Push the branch and open a PR.**
