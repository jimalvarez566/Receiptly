# Admin vs Member Roles — Design

**Date:** 2026-10-28
**Branch:** `feature/admin-member-roles` (from `main`, after PR #3 RLS)
**Status:** Draft for approval

## Intent

Today every member of a business can do everything, including adding and
removing other members, and see every receipt. Introduce two roles so a
business can let employees submit expenses without giving them control of the
business or visibility into colleagues' receipts.

## Decisions (from the user)

- **Admins** manage policies and members, and approve or reject receipts.
- **Members** upload receipts and view receipts. They see **only their own**.
- Only admins approve or reject receipts (a member must not clear their own flag).
- Every denial returns a **specific, human-readable message** (e.g. "You can't
  view this receipt"), and the frontend displays it.

## Calls I made (flag any you disagree with)

1. Roles are exactly `admin` and `member`. Existing `owner` rows become `admin`;
   no separate owner tier. The creator of a business is its first `admin`.
2. A business must always keep at least one admin: removing or demoting the
   last admin is rejected (400, "A business needs at least one admin").
3. Inside your own business, a member requesting someone else's receipt gets
   **403** "You can't view this receipt". A receipt in another business still
   returns plain **404**.
4. Dashboard totals and charts for a member reflect only their own receipts
   (the dashboard reads the list endpoint, so this follows from scoping it).
5. Duplicate detection continues to compare against **all receipts in the
   business**, not just the uploader's.
6. `analyze` (re-run the fraud pipeline) is admin-only. `explain` is allowed
   for any receipt the caller can view.
7. A duplicate flag's `details` contains `duplicate_receipt_id`, the id of
   another (possibly another member's) receipt. When a member views their own
   receipt, remove `duplicate_receipt_id` from flag details unless that receipt
   is also theirs. The flag itself and its description still show.
8. Members can see the member list (user ids and roles; the API has no names or
   emails) but not change it. (Revisit if you want it admin-only.)

## Goals

- Role stored on `memberships.role` ∈ {`admin`,`member`}; enforced in the backend
  on every request. Frontend hiding of controls is convenience only.
- A reusable dependency `require_admin` (and a helper for "can view receipt")
  so the upcoming policy-editing endpoints plug straight in.
- Role-aware error messages end to end.

## Non-goals

- Policy editing endpoints (next backlog item; this adds only the guard).
- Invitations for users without accounts, a change-role UI beyond what is needed
  to exercise the backend, RLS policies keyed on role (backend is the only
  accessor; RLS stays deny-all).
- Per-receipt sharing, additional roles (auditor, etc.).

## Backend

### Migration (`c3d4e5f6a7b8`, after `b2c3d4e5f6a7`)
- `UPDATE memberships SET role='admin' WHERE role='owner'`.
- Add `CHECK (role IN ('admin','member'))`; change model default to `"member"`.
- Downgrade: drop the check, set `admin` back to `owner`.
- Update `create_tenant` to insert the creator as `admin`.

### Permissions (`app/auth.py`)
- `ROLE_ADMIN = "admin"`, `ROLE_MEMBER = "member"`.
- `require_admin(context=Depends(get_current_context)) -> RequestContext`:
  403 "Only admins can do this" unless `context.role == "admin"`. Endpoints pass
  a specific message via a small factory `require_admin("approve or reject receipts")`
  → "Only admins can approve or reject receipts".

### Endpoint matrix

| Endpoint | admin | member |
|---|---|---|
| `POST /receipts/upload` | any | any (stamps `submitted_by_user_id`) |
| `GET /receipts` | all in business | only `submitted_by_user_id == me` |
| `GET /receipts/{id}` | any in business | own; otherwise 403 "You can't view this receipt" |
| `POST /receipts/{id}/explain` | any in business | own; otherwise same 403 |
| `POST /receipts/{id}/analyze` | any in business | 403 "Only admins can re-run fraud analysis" |
| `PATCH /receipts/{id}/review` | any in business | 403 "Only admins can approve or reject receipts" |
| `GET /tenants/{id}/members` | yes | yes |
| `POST /tenants/{id}/members` | yes | 403 "Only admins can manage members" |
| `DELETE /tenants/{id}/members/{uid}` | yes (not last admin) | 403 same |
| new `PATCH /tenants/{id}/members/{uid}` `{role}` | yes (not demoting last admin) | 403 same |

Receipt in another business → 404 for everyone (unchanged). A member listing
with `status=` filters still applies on top of the own-receipts filter.

### Tests (new `tests/test_roles.py`, extend existing)
Each cell of the matrix above for both roles; member sees only own in list and
dashboard-style listing; other-member receipt → 403 with exact message;
cross-business → 404; last-admin remove/demote → 400; promote then act;
migration maps `owner`→`admin`; existing 113 tests updated where they assumed
`owner`.

## Frontend

- `api.ts`: surface the backend `detail` string in thrown errors (today generic);
  add `role` to `Membership` handling and `updateMemberRole`.
- A small `useRole()` (from `TenantProvider` memberships + active tenant) giving
  `isAdmin`.
- Hide admin-only controls for members: approve/reject buttons on receipt
  detail, re-run analysis, any member-management UI. Receipts list/dashboard
  need no change (the backend scopes them).
- Show server messages in an inline error banner on each page that performs an
  action or load (receipt detail, receipts list, upload, dashboard, business
  select). Keep the existing 401 → sign-out behavior.
- Sidebar shows the user's role under the business name.

## Verification

- `pytest` all green, including new role tests.
- Manual: with Northwind, add a second account as `member`; as member verify
  only own receipts appear, direct URL to another's receipt shows "You can't
  view this receipt", no approve/reject buttons; as admin everything works.

## Rollout

One migration; no config changes. Existing `owner` users become admins, so
nobody loses access. Must be applied to the hosted database with
`alembic upgrade head`.
