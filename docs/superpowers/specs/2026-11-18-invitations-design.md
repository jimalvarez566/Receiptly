# Shareable Invitation Links — Design

**Date:** 2026-11-18
**Branch:** `feature/invitations` (from `main` @ 63e7549)
**Status:** Draft for approval

## Intent

Admins can only add people who already have an account ("No account for that
email; ask them to sign up first"). Let an admin invite someone with no account
yet by creating a **shareable, single-use link** they send by whatever means they
like. Opening the link, signing up (or logging in) and clicking Join makes the
person a member of the business with the role the admin chose.

## Decisions (from the user)

- **Shareable invite links first.** The app does **not** send emails in this
  piece (Supabase's built-in email only reaches your own team's addresses, and
  real invitation emails need a custom email provider; that is a later,
  separate step).
- **Approach 1:** our own `invitations` table with a secret token (not a
  stateless signed link, not Supabase's own invite links).

## Approaches considered

1. **Own table + secret token (chosen).** Single-use, expiring, revocable,
   works for new and existing users, independent of any email setup.
2. **Self-contained signed token, no table.** No new table, but cannot be
   single-use or revoked; a leaked link works until it expires. Rejected.
3. **Supabase `generate_link` invite.** Link proves email ownership, but only
   works for brand-new accounts, creates unclaimed accounts, and needs Supabase
   redirect/password-setup handling in the frontend. Rejected.

## Why not match on email address

Signup is open and email confirmation is off, so an account's email is not
proof of ownership. "Whoever signs up as alex@company.com joins" would let a
stranger take the seat. The secret token in the link is the protection, so
the invitation is **not tied to an email address**: whoever holds the link can
use it once.

## Goals / non-goals

**Goals:** admin creates, lists and revokes invite links; invitee previews
the invite, authenticates, and joins; single-use, expiring, concurrency-safe;
readable error messages; tokens never stored in plain text or logged.

**Non-goals:** sending emails; binding an invite to an email address; a full
member-management UI (the Team page below shows invitations only);
per-business caps on invites; changing roles at accept time; multi-use links.

## Calls I made (flag any you disagree with)

1. **Expiry 7 days**, configurable `INVITE_TTL_DAYS` (default 7).
2. **Roles:** the admin picks `member` (default) or `admin` when creating the
   invite (an admin can already promote members, so this adds no new power).
3. **Token:** `secrets.token_urlsafe(32)`; only its SHA-256 hex digest is stored
   (`token_hash`, unique). The plain token is returned **once**, at creation,
   and cannot be shown again (create a new invite instead).
4. **Already a member:** accepting does not consume the invite and does not
   change the existing role; the response says they're already a member.
5. **List shows all statuses** (pending, used, expired, revoked), newest first,
   up to 50, so admins can see what happened. Status is derived, not stored
   separately.
6. **Public preview is rate-limited** (`60/minute` per IP via the existing
   `slowapi` limiter). Tokens are 256-bit so guessing is not a practical risk;
   the limit is cheap hygiene.
7. **Existing email-based add-member stays** as is.
8. **Token handling in the browser:** on `/join?token=…` the page stores the
   token in `localStorage` (key `pendingInviteToken`) and removes it from the
   address bar (`history.replaceState`), so it survives the signup/login detour
   — including a confirmation-email link opened in a new tab if email
   confirmation is ever switched on — but isn't left in the URL or history. It
   is cleared as soon as the invite reaches any final outcome (joined, already
   a member, invalid/expired/used/revoked) or the user chooses "Continue to the
   app".
9. **Revoke** is a soft delete (`revoked_at`), so the audit trail remains.
10. **RLS:** the new table gets row-level security enabled in its migration
    (deny-all; the backend role bypasses it), like every other table.

11. **Order of checks in accept:** (a) unknown token → 404; (b) if the caller is
    already a member of that business → 200 `already_member: true` **whatever
    the invite's status** (so a double-click or a refresh after joining is
    harmless, and an already-in person is never shown an error), invite not
    consumed; (c) otherwise revoked/used/expired → 410; (d) otherwise join.
12. **Two invites for the same person and business racing:** the unique
    membership constraint is caught (`IntegrityError`) and treated as
    `already_member: true`, never a 500. Accepting adds a member, so it cannot
    affect the last-admin rule and needs no admin-row locking.
13. **When a logged-in user is sent to `/join`:** whenever a `pendingInviteToken`
    exists and the user lands anywhere other than `/join`, they are redirected
    to `/join` once. `/join` always ends the pending state (see call 8) and
    always offers "Continue to the app", so a stale token can't trap anyone or
    be silently dropped.
14. **Team page freshness:** the list is fetched on page load and after each
    action; no polling. More than 50 invitations: only the newest 50 show
    (acceptable; no pagination in this piece).
15. **Rate-limit key:** the preview limit uses the app's existing
    `get_remote_address` key. Behind a proxy that would be the proxy's address,
    so the limit should be revisited when the app is deployed (production
    section of the backlog). Tokens are unguessable, so this only affects
    abuse protection, not safety.

## Data model

New table `invitations` (Alembic migration `d4e5f6a7b8c9`, after
`c3d4e5f6a7b8`):

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | int FK → `tenants.id` ON DELETE CASCADE | indexed |
| `role` | varchar(20) | CHECK `IN ('admin','member')` (`ck_invitations_role`) |
| `token_hash` | varchar(64) | unique, not null |
| `created_by_user_id` | uuid | not null |
| `created_at` | timestamp | default now (UTC, naive like the other tables) |
| `expires_at` | timestamp | not null |
| `accepted_at` | timestamp | null until used |
| `accepted_by_user_id` | uuid | null until used |
| `revoked_at` | timestamp | null unless revoked |

Derived status: `revoked` if `revoked_at`; else `used` if `accepted_at`; else
`expired` if `expires_at <= now`; else `pending`.
The migration also runs `ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY`
(downgrade drops the table). Add the model to `app/models/__init__.py`;
`tests/test_rls_coverage.py` must keep passing.

## API (new router `app/api/v1/invitations.py`, prefix `/api/v1`)

Admin endpoints (all `require_admin("manage members")`, tenant from the path with
the existing `_require_membership` path/header check pattern):

| Endpoint | Behavior |
|---|---|
| `POST /tenants/{tenant_id}/invitations` | body `{role}` (`member` default); 201 `{id, role, expires_at, token}` — the only time `token` is returned |
| `GET /tenants/{tenant_id}/invitations` | list (max 50, newest first): `{id, role, status, created_at, expires_at, created_by_user_id, accepted_by_user_id}` — never the token or hash |
| `DELETE /tenants/{tenant_id}/invitations/{invitation_id}` | revoke (204); already used → 409 "That invitation was already used"; another business's id → 404 "Invitation not found" |

Invitee endpoints (the token is in the path):

| Endpoint | Auth | Behavior |
|---|---|---|
| `GET /invitations/{token}` | none (rate-limited) | `{tenant_name, role, expires_at}` for a valid pending invite. Unknown → 404 "This invitation link isn't valid"; revoked → 410 "This invitation was revoked"; used → 410 "This invitation has already been used"; expired → 410 "This invitation has expired" |
| `POST /invitations/{token}/accept` | login (`get_current_user`), no `X-Tenant-ID` | locks the row (`FOR UPDATE`) and applies the order in call 11: unknown → 404; caller already a member → 200 `already_member: true` (invite not consumed, whatever its status); else revoked/used/expired → 410 (same messages as the preview); else creates the `Membership` with the invite's role and sets `accepted_at`/`accepted_by_user_id`. Returns `{tenant_id, tenant_name, role, already_member}`. A concurrent double-accept by two different people: the second gets 410 "already been used" |

Tokens/hashes are never logged. Errors use the existing `{"detail": ...}` shape.

## Frontend

- `api.ts`: `createInvitation(tenantId, role)`, `listInvitations(tenantId)`,
  `revokeInvitation(tenantId, id)`, `previewInvitation(token)` (works without a
  session — no auth header), `acceptInvitation(token)`; matching types.
- **Team page** (`/team`, admin-only, sidebar link "Team"): "Create invite link"
  (role select + button) → shows the full link
  `${window.location.origin}/join?token=…` with a Copy button and a note
  "This link is shown once. Anyone who has it can join once, until it expires or
  you revoke it."; below, the invitation list with a status badge and a Revoke
  button (pending only) with a confirmation like policy delete.
- **Join page** (`/join`, available both logged out and logged in): reads the
  token (query string, else `sessionStorage`), stores it in `sessionStorage`,
  strips it from the URL, previews the invite ("You've been invited to <name>
  as <role>"). Logged out: "Create an account" and "Log in" buttons (existing
  pages); after login/signup the user is returned to `/join`. Logged in: "Join
  <name>" button → accept → set that business active, refresh memberships,
  clear the stored token, go to `/`. Invalid/expired/used/revoked: the server's
  message in a readable panel, with a link to continue to the app.
- Routing: `/join` must work for a logged-out user, for a logged-in user with no
  active business (today every route there shows SelectBusiness), and for a
  logged-in user with an active business. A pending stored token sends a user
  who just authenticated to `/join`.
- Lint rules as before (no new errors beyond the 5 known; no synchronous
  `setState` at the top of an effect; helpers outside component files).

## Tests

- Migration: upgrade/downgrade on a throwaway local database; model check
  constraint; `test_rls_coverage` passes.
- API: create (member/admin; non-admin → exact 403 "Only admins can manage
  members"); token returned only at creation and never in the list; hash stored,
  not the token; preview for each status with exact messages; accept happy path
  creates the membership with the invite's role; accept twice → 410; accept by
  an existing member → 200 `already_member` and the invite stays pending;
  expired/revoked → 410; unknown token → 404; accept without login → 401;
  revoke (admin, other business → 404, used → 409); list limited to the
  caller's business; two concurrent accepts of one invite produce one
  membership (two real sessions, like the last-admin test); tokens/hashes
  absent from logs (`caplog`); preview rate limit returns 429 after the limit.
- Frontend: build + lint. Local end-to-end on a throwaway localhost DB (real
  migrations, admin + a new member token flow) like earlier features.

## Rollout

One migration (`invitations` table + RLS) that must be applied to the live
database with `alembic upgrade head` after merge (with the user's OK). No new
config required (`INVITE_TTL_DAYS` defaults to 7). Nothing changes for existing
members.
