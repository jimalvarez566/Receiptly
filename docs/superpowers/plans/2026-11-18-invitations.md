# Shareable Invitation Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin create single-use, expiring, revocable invite links; let someone with no account open one, sign up or log in, and join the business with the role the admin chose.

**Architecture:** A new `invitations` table stores only a SHA-256 hash of a random token. Admin endpoints (create/list/revoke) hang off `/tenants/{tenant_id}/invitations`; two invitee endpoints (public preview, authenticated accept) live under `/invitations/{token}`. The frontend gets an admin Team page and a `/join` page that remembers a pending token in `localStorage` across the signup/login detour.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, slowapi (existing limiter), pytest-asyncio; React 19 + TS strict + Tailwind (no frontend test runner).

**Spec:** `docs/superpowers/specs/2026-11-18-invitations-design.md` (the "Calls I made" 1–15 there are binding decisions).

## Global Constraints

- Token: `secrets.token_urlsafe(32)`; store only `hashlib.sha256(token.encode()).hexdigest()` in `token_hash` (varchar(64), unique). The plain token is returned **only** by the create endpoint; never in list/preview/accept responses; **never logged**; no log call may include a token or hash.
- Expiry: `INVITE_TTL_DAYS: int = 7` in `app/config.py`. Timestamps are naive UTC like the other tables (`datetime.utcnow()`).
- Roles: `role` ∈ `admin|member`, default `member`; DB check constraint `ck_invitations_role`.
- Status is derived, never stored: `revoked` if `revoked_at`; else `used` if `accepted_at`; else `expired` if `expires_at <= utcnow`; else `pending`.
- Admin endpoints use `require_admin("manage members")` (member 403: `"Only admins can manage members"`) and the existing path-vs-header tenant check (`_require_membership(tenant_id, context)` in `app/api/v1/tenants.py`; mismatch → 403). Another business's invitation id → 404 `"Invitation not found"`; revoking a used invite → 409 `"That invitation was already used"`; revoking an already-revoked invite is idempotent 204.
- Preview/accept messages (exact): unknown → 404 `"This invitation link isn't valid"`; revoked → 410 `"This invitation was revoked"`; used → 410 `"This invitation has already been used"`; expired → 410 `"This invitation has expired"`.
- Accept order (call 11): unknown → 404; caller already a member of that business → 200 `already_member: true` whatever the invite's status, invite untouched; else revoked/used/expired → 410; else create membership with the invite's role, set `accepted_at`/`accepted_by_user_id`. Row locked with `SELECT … FOR UPDATE`; a duplicate-membership `IntegrityError` is treated as `already_member: true` (never a 500). Accept needs login (`get_current_user`) and no `X-Tenant-ID`.
- Preview is public (no auth) and rate-limited `60/minute` with the existing limiter (`from app.api.v1.receipts import limiter`; the route needs a `request: Request` parameter).
- List: max 50, newest first, own business only; fields `{id, role, status, created_at, expires_at, created_by_user_id, accepted_by_user_id}`.
- New table gets `ENABLE ROW LEVEL SECURITY` in its migration (`d4e5f6a7b8c9`, `down_revision = "c3d4e5f6a7b8"`); `tests/test_rls_coverage.py` must pass. No new dependency.
- Frontend: link is `${window.location.origin}/join?token=<token>`; pending token in `localStorage` key `pendingInviteToken` (wrap storage access in try/catch like `src/lib/activeTenant.ts`); the token is removed from the address bar with `history.replaceState`; cleared on every final outcome or "Continue to the app". Lint: no new errors beyond the 5 known (TenantProvider.tsx, ReceiptDetail.tsx ×2, ReceiptsList.tsx, ui/button.tsx react-refresh); no synchronous `setState` at the top of an effect; non-component exports stay out of component files.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never run alembic or any DB command against `backend/.env`'s database; backend tests use the local Postgres only. Applying the migration to the live database, pushing, and the PR need the user's yes (controller).

## Review Focus

- Two different people accepting the same invite at the same moment → exactly one membership, the other gets 410 (two real sessions; Task 3 test).
- The same user accepting two different invites for the same business concurrently → no 500, second is `already_member` (Task 3 test).
- A used/expired/revoked invite opened by someone who is already a member → 200 `already_member`, not an error (Task 3 test).
- Token/hash never appear in logs (success and failure paths) or in any response except create (Tasks 2–3 `caplog` tests).
- A member (non-admin) calling every admin invitation endpoint, including a non-existent invitation id → exact 403 (Task 2 test).
- A stale `pendingInviteToken` can't trap a user: every outcome on `/join` clears it and offers "Continue to the app" (Task 5 verification).

---

## File Structure

- Create `backend/app/models/invitation.py`; modify `backend/app/models/__init__.py`, `backend/app/config.py`.
- Create `backend/alembic/versions/d4e5f6a7b8c9_invitations.py`.
- Create `backend/app/schemas/invitation.py`, `backend/app/services/invitations.py` (token + status helpers), `backend/app/api/v1/invitations.py`; modify `backend/app/main.py`.
- Tests: `backend/tests/test_invitation_model.py`, `test_invitations_admin_api.py`, `test_invitations_accept_api.py`.
- Frontend: modify `src/lib/api.ts`, `src/App.tsx`, `src/components/Layout.tsx`; create `src/lib/pendingInvite.ts`, `src/pages/Team.tsx`, `src/pages/Join.tsx`.

---

### Task 1: Model, migration, config

**Files:**
- Create: `backend/app/models/invitation.py`, `backend/alembic/versions/d4e5f6a7b8c9_invitations.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/config.py`
- Test: `backend/tests/test_invitation_model.py`

**Interfaces:**
- Produces: `class Invitation(Base)` (`__tablename__ = "invitations"`) with columns `id, tenant_id, role, token_hash, created_by_user_id, created_at, expires_at, accepted_at, accepted_by_user_id, revoked_at` exactly as in the spec's Data model table, `CheckConstraint("role IN ('admin','member')", name="ck_invitations_role")`, unique on `token_hash`, index on `tenant_id`, FK `tenants.id` `ondelete="CASCADE"`; exported from `app.models`; `settings.INVITE_TTL_DAYS = 7`.

- [ ] **Step 1: Write failing tests** (`db_session`-based): `test_invitation_roundtrip_and_defaults` (insert with required fields; `accepted_at`/`revoked_at`/`accepted_by_user_id` are `None`; `created_at` set); `test_invitation_rejects_unknown_role` (`IntegrityError`); `test_token_hash_is_unique` (`IntegrityError`); `test_deleting_tenant_cascades_invitations`; `test_invitations_are_in_the_rls_migration` is already covered by `tests/test_rls_coverage.py` (it will fail until the migration lists the table — confirm that failure in Step 2).
- [ ] **Step 2: Run** `cd backend && python3 -m pytest tests/test_invitation_model.py tests/test_rls_coverage.py -q` — expect FAIL (model missing; after adding the model but before the migration, the RLS coverage test fails).
- [ ] **Step 3: Implement** the model (mirror `membership.py` style), export it, add the setting, and write the migration: `upgrade` creates the table + indexes + check constraint then `op.execute('ALTER TABLE public."invitations" ENABLE ROW LEVEL SECURITY')`; `downgrade` drops the table. The RLS migration text must contain the literal `ENABLE ROW LEVEL SECURITY` and the table name in quotes so the coverage test passes.
- [ ] **Step 4: Verify migration on a throwaway local database** `receiptly_inv_mig` (localhost only; set BOTH `DATABASE_URL` and `DATABASE_URL_DIRECT` to it and abort unless the host is localhost): `alembic upgrade head` succeeds, `relrowsecurity` is true for `invitations`, `alembic downgrade -1` drops it, upgrade again; then drop the database. Then run the full suite — green.
- [ ] **Step 5: Commit** `feat: invitations table with RLS`.

### Task 2: Admin endpoints (create, list, revoke)

**Files:**
- Create: `backend/app/services/invitations.py`, `backend/app/schemas/invitation.py`, `backend/app/api/v1/invitations.py`
- Modify: `backend/app/main.py` (register the router with prefix `/api/v1`, beside the others)
- Test: `backend/tests/test_invitations_admin_api.py`

**Interfaces:**
- Consumes: `Invitation` (Task 1); `require_admin`, `RequestContext` (`app/auth.py`); `_require_membership` from `app/api/v1/tenants.py` (import it, or move it to a shared place without changing its behavior).
- Produces:
  - `app/services/invitations.py`: `def new_token() -> str`; `def hash_token(token: str) -> str`; `def invitation_status(inv: Invitation, now: datetime | None = None) -> Literal["pending","used","revoked","expired"]`.
  - `app/schemas/invitation.py`: `InvitationCreate(role: Literal["admin","member"] = "member")`; `InvitationCreated(id, role, expires_at, token)`; `InvitationOut(id, role, status, created_at, expires_at, created_by_user_id, accepted_by_user_id)`; `InvitationPreview(tenant_name, role, expires_at)`; `InvitationAccepted(tenant_id, tenant_name, role, already_member)`.
  - Router (no prefix of its own beyond `/tenants/{tenant_id}/invitations` paths for these three): `POST /tenants/{tenant_id}/invitations` (201), `GET /tenants/{tenant_id}/invitations`, `DELETE /tenants/{tenant_id}/invitations/{invitation_id}` (204).

- [ ] **Step 1: Write failing tests** (helpers like `tests/test_roles_members.py`: seeded tenant with an admin and a member; HS256 tokens via `make_token`): `test_admin_creates_member_and_admin_invites` (201; response has `token`; DB row stores `hash_token(token)` and not the token; `expires_at` ≈ now + 7 days); `test_default_role_is_member`; `test_member_gets_exact_403_on_all_three_endpoints` (POST/GET/DELETE incl. non-existent id `99999` → `"Only admins can manage members"`); `test_list_never_contains_token_or_hash` (check the raw JSON text for the token string and the hash); `test_list_is_newest_first_limited_to_50_and_own_business_only`; `test_status_values` (pending/used/revoked/expired rows each show the right derived status — insert rows directly); `test_revoke_pending_sets_revoked_at_and_is_idempotent`; `test_revoke_used_is_409_with_exact_message`; `test_revoke_other_business_invitation_is_404_and_unchanged`; `test_tenant_path_must_match_header` (403 like the other tenant routes); `test_token_and_hash_not_logged` (`caplog` DEBUG over create/list/revoke: no record contains the token or its hash); unit tests for `hash_token` (stable, 64 hex chars), `new_token` (distinct, ≥ 43 chars), `invitation_status` (all four plus boundary `expires_at == now` → expired, revoked beats used).
- [ ] **Step 2: Run** `python3 -m pytest tests/test_invitations_admin_api.py -q` — FAIL.
- [ ] **Step 3: Implement** helpers, schemas, the three endpoints and registration. Use `INVITE_TTL_DAYS` for `expires_at`; `await db.flush()` like other routers.
- [ ] **Step 4: Run** the file — PASS; then the full suite — green.
- [ ] **Step 5: Commit** `feat: admin endpoints to create, list and revoke invitations`.

### Task 3: Invitee endpoints (preview, accept)

**Files:**
- Modify: `backend/app/api/v1/invitations.py`
- Test: `backend/tests/test_invitations_accept_api.py`

**Interfaces:**
- Consumes: Task 2's helpers/schemas; `get_current_user`/`CurrentUser` (`app/auth.py`); `limiter` from `app.api.v1.receipts`.
- Produces: `GET /invitations/{token}` → `InvitationPreview` (public, `@limiter.limit("60/minute")`, takes `request: Request`); `POST /invitations/{token}/accept` → `InvitationAccepted` (200 for both a new join and `already_member`; use 200 for both).

- [ ] **Step 1: Write failing tests:** `test_preview_valid_invite_returns_name_role_expiry_without_login`; `test_preview_error_messages` (unknown 404, revoked/used/expired 410 with the exact texts); `test_preview_does_not_leak_token_or_ids`; `test_preview_is_rate_limited_to_429` (101st-style loop is too slow — instead monkeypatch the limit or call 61 times; assert a 429 appears once the limit is exceeded; reset the limiter storage between tests so other tests aren't affected); `test_accept_creates_membership_with_the_invite_role_and_marks_used` (member and admin invites; `accepted_at`, `accepted_by_user_id` set; membership row exists; response `already_member == false`, `tenant_id`/`tenant_name`/`role` correct); `test_accept_requires_login_401`; `test_accept_twice_by_another_user_is_410_used`; `test_accept_expired_and_revoked_are_410_with_exact_messages`; `test_accept_unknown_token_404`; `test_accept_by_existing_member_is_200_already_member_and_leaves_invite_pending`; `test_already_member_wins_even_when_invite_is_used_expired_or_revoked` (200 `already_member: true` for each); `test_existing_role_is_not_changed_by_accept` (an admin accepting a `member` invite stays admin); `test_two_people_accepting_concurrently_make_one_membership` and `test_same_user_two_invites_concurrently_is_not_a_500` (two real `AsyncSession`s from the test engine in `tests/conftest.py`, calling the accept function directly with hand-built user objects like `tests/test_roles_members.py`'s concurrency tests; second session blocks on the row lock, then sees the committed result); `test_token_not_logged_on_preview_or_accept` (`caplog`).
- [ ] **Step 2: Run** `python3 -m pytest tests/test_invitations_accept_api.py -q` — FAIL.
- [ ] **Step 3: Implement** both routes per the accept order in Global Constraints; hash the path token and look it up by `token_hash`; for accept, `select(...).with_for_update()` on the invitation, then check membership, then status, then insert `Membership` inside a `try/except IntegrityError` (use a savepoint so the session stays usable) mapping to `already_member`.
- [ ] **Step 4: Run** the file — PASS; full suite — green.
- [ ] **Step 5: Commit** `feat: public invitation preview and authenticated accept`.

### Task 4: Frontend — API client and Team page

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`
- Create: `frontend/src/pages/Team.tsx`

**Interfaces:**
- Consumes: Task 2–3 endpoints; `useRole()`; the `AdminOnly` wrapper already in `App.tsx`; `ApiError` messages.
- Produces in `api.ts`: `export type InviteRole = 'admin' | 'member'`; `export type InviteStatus = 'pending' | 'used' | 'revoked' | 'expired'`; `export interface Invitation { id: number; role: InviteRole; status: InviteStatus; created_at: string; expires_at: string; created_by_user_id: string; accepted_by_user_id: string | null }`; `export interface InvitationCreated { id: number; role: InviteRole; expires_at: string; token: string }`; `export interface InvitationPreview { tenant_name: string; role: InviteRole; expires_at: string }`; `export interface InvitationAccepted { tenant_id: number; tenant_name: string; role: InviteRole; already_member: boolean }`; `api.createInvitation(tenantId: number, role: InviteRole)`, `api.listInvitations(tenantId: number)`, `api.revokeInvitation(tenantId: number, id: number)` (204 → `void`), `api.previewInvitation(token: string)`, `api.acceptInvitation(token: string)`.

- [ ] **Step 1: Implement** the api additions (follow the policy-rules methods; `previewInvitation` and `acceptInvitation` URL-encode the token).
- [ ] **Step 2: Implement `Team.tsx`** (`export function Team()`): active tenant id from `useTenant()`; "Create invite link" with a role select (default member) → shows `${window.location.origin}/join?token=${token}` in a read-only field with a Copy button (clipboard API with a "Copied" confirmation and a fallback that selects the text) and the note "This link is shown once. Anyone who has it can join once, until it expires or you revoke it."; the invitation list (role, status badge, created/expires dates) with Revoke on pending rows behind an inline confirm ("Revoke this invite link? Anyone who hasn't used it yet will no longer be able to." Cancel/Revoke); load on mount (no synchronous setState at the top of the effect) and refresh after each action; errors inline via `err.message`; in-flight buttons disabled.
- [ ] **Step 3: Route and nav:** `/team` inside the `Layout` route wrapped in `AdminOnly`; "Team" link in the sidebar for admins only (same pattern as Policies).
- [ ] **Step 4: Verify** `cd frontend && npm run build` passes; `npm run lint` shows only the 5 known errors.
- [ ] **Step 5: Commit** `feat(frontend): admin Team page to create and revoke invite links`.

### Task 5: Frontend — Join page and routing

**Files:**
- Create: `frontend/src/lib/pendingInvite.ts`, `frontend/src/pages/Join.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api.previewInvitation`, `api.acceptInvitation`, `InvitationPreview`, `InvitationAccepted` (Task 4); `useAuth()`, `useTenant()` (`setActiveTenant`, `refreshMemberships`).
- Produces in `lib/pendingInvite.ts`: `getPendingInvite(): string | null`, `setPendingInvite(token: string): void`, `clearPendingInvite(): void` — `localStorage` key `pendingInviteToken`, every access in try/catch (storage may be disabled).

- [ ] **Step 1: Implement `pendingInvite.ts`.**
- [ ] **Step 2: Implement `Join.tsx`** (`export function Join()`): token = `?token=` query param, else the stored one; if present from the URL, store it and `history.replaceState` the query away; no token at all → a readable panel "This invitation link isn't valid" with "Continue" (to `/` or `/login`). Fetch the preview (no synchronous setState at the top of the effect). Valid preview shows "You've been invited to <tenant_name> as <role>" and expiry. **Logged out** (`useAuth().session == null`): buttons "Create an account" (`/signup`) and "Log in" (`/login`); the stored token is what carries the invite across. **Logged in:** "Join <tenant_name>" → `acceptInvitation(token)` → `refreshMemberships()`, `setActiveTenant(result.tenant_id)`, `clearPendingInvite()`, navigate to `/` (if `already_member`, show "You're already a member of <name>" with a Continue button instead of silently redirecting, and clear the pending token). Any server error (404/410/other): show `err.message` in a panel, `clearPendingInvite()`, and a "Continue to the app" link. All buttons disabled while in flight.
- [ ] **Step 3: Routing in `App.tsx`:** add `/join` to the logged-out routes (before the `*` redirect to `/login`); in `AuthedRoutes`, make `/join` reachable both when `activeTenantId == null` (today every path shows `SelectBusiness`) and when a business is active; and add a one-time redirect: a logged-in user who lands on any other path while `getPendingInvite()` is non-null is sent to `/join` (use a small component with `Navigate`, not an effect with synchronous `setState`). After login/signup the user ends up on `/join` this way.
- [ ] **Step 4: Verify** `npm run build` passes; `npm run lint` only the 5 known errors; re-read the routing against these cases and list them in your report: logged-out + token → preview with sign-up/log-in; logged-out signs up → lands on `/join` with the token; logged-in with no business + token → `/join` works (not SelectBusiness); logged-in with a business + token → `/join`; stale token → panel + cleared + Continue works; already member → friendly message.
- [ ] **Step 5: Commit** `feat(frontend): join page and invite hand-off through signup and login`.

### Task 6: Docs and local end-to-end

**Files:** Modify `README.md` (a short "Inviting people" note: Team page, link shown once, single use, 7-day expiry, revoke; no emails sent yet; the link isn't tied to an email address — whoever has it can use it once), `TODO.md` (tick the invitation-tokens item; add a new unchecked item "Email invitations through a custom SMTP provider").

- [ ] **Step 1: Docs** and commit `docs: describe invitation links`.
- [ ] **Step 2: Full suite + build:** `cd backend && python3 -m pytest -q` green; `cd ../frontend && npm run build` passes.
- [ ] **Step 3: Local end-to-end (no Supabase):** throwaway localhost database `receiptly_inv_e2e` (migrations via `alembic upgrade head` with BOTH `DATABASE_URL` and `DATABASE_URL_DIRECT` set to it; abort unless the host is localhost), a launcher in the scratchpad that stubs Supabase Storage/email lookup (model it on the earlier e2e scratch scripts under `/private/tmp/claude-501/-Users-syonchau-ai-fraud-payroll/67a86d64-9537-4ba5-9a97-20ee4b06b8da/scratchpad/` — `img_e2e/` or `policy_e2e/`; port 8768), minted HS256 tokens, real HTTP: admin creates a member invite and an admin invite; preview works with no login; a brand-new user accepts the member invite → member; list shows used; the same link again by someone else → 410; revoke a pending invite → preview 410 revoked; a member cannot create/list/revoke (exact 403); an expired invite (set `expires_at` directly in the throwaway DB) → 410; already-member accept → 200 `already_member`; token absent from the list JSON. Report PASS/FAIL per check, stop the server, drop the database; keep the scripts out of git.
- [ ] **Step 4 (needs the user's yes, run by the controller):** after merge, apply the migration to the live database (`alembic upgrade head`: pre-flight `select version_num from alembic_version` must show `c3d4e5f6a7b8`; afterwards verify the table exists with RLS on and that your public key reads nothing from it); push the branch and open the PR.
