# Supabase Multi-Tenant Auth Foundation — Design

**Date:** 2026-09-08
**Branch:** `feature/supabase-multitenant-auth`
**Status:** Approved for implementation planning

## Goal

Make the expense fraud detection backend genuinely multi-tenant: multiple
businesses use the same deployment, each with multiple users, with strict data
isolation between businesses. Move persistence, authentication, and file storage
to a Supabase project the team has already created.

## Guiding decisions (from brainstorming)

- **Supabase = data + auth + file storage. FastAPI = processing + orchestration.**
  All data lives in Supabase Postgres. FastAPI connects to that same database and
  does the work Supabase cannot run (OCR via Tesseract, scikit-learn, PDF
  handling, third-party AI API calls, the fraud pipeline).
- **Single access path.** The frontend calls Supabase only to log in and obtain a
  JWT. Every data read and write goes through FastAPI, which verifies the JWT and
  scopes every query by the active tenant. This is the one chokepoint to secure
  and test.
- **No Row-Level Security in this phase.** App-level tenant scoping only. RLS is
  added in Phase 2 as defense-in-depth once the schema has settled.
- **Users belong to multiple businesses** via a `memberships` join table. The
  client sends the active business per request.
- **One role for now.** `memberships.role` exists (default `owner` for the
  creator, `member` for added users) but is not enforced. Role checks are Phase 2.
- **Onboarding: self-serve create + invite by existing email only.** Any
  authenticated user can create a business. To add someone, that person must
  already have a Supabase account; the owner adds them by email. No invite
  tokens or invite emails in this phase.
- **Migrations: keep Alembic** for domain tables (autogenerate from SQLAlchemy
  models). Introduce `supabase/migrations/*.sql` only when RLS and Storage
  policies arrive in Phase 2.

## Architecture

```
Frontend ──login/signup──────────────> Supabase Auth (GoTrue)   → issues JWT + refresh token
Frontend ──all other requests────────> FastAPI                   → verifies JWT, resolves tenant, scopes queries
FastAPI  ──SQLAlchemy async──────────> Supabase Postgres         → same database, via DATABASE_URL
FastAPI  ──storage API───────────────> Supabase Storage          → receipt image files
FastAPI  ──server-side secrets───────> Claude / Gemini APIs      → Phase 2
```

## Data model

`auth.users` is owned by Supabase (email, password hash, etc.). We never write to
it. We store its `id` (uuid) as a plain uuid column in our tables. No foreign key
across to the `auth` schema — integrity of `user_id` values is enforced in
application code. This keeps Alembic unaware of the `auth` schema.

### New tables

**`tenants`** — a business

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(255) NOT NULL | |
| `created_by_user_id` | uuid NOT NULL | `auth.users.id` of the creator |
| `created_at` | timestamp NOT NULL | default now |

**`memberships`** — a user's link to a business

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | int NOT NULL | FK → `tenants.id`, ON DELETE CASCADE |
| `user_id` | uuid NOT NULL | `auth.users.id` |
| `role` | varchar(20) NOT NULL | default `'owner'`; not enforced this phase |
| `created_at` | timestamp NOT NULL | default now |

Constraints/indexes: `UNIQUE (tenant_id, user_id)`; index on `user_id`;
index on `tenant_id`.

### Changed tables

**`receipts`**
- add `tenant_id` int NOT NULL, FK → `tenants.id`
- add `submitted_by_user_id` uuid NOT NULL — the auth user who uploaded it
- add index on `tenant_id`
- `employee_id` stays (optional link to an `employees` roster row)

**`policy_rules`**
- add `tenant_id` int NOT NULL, FK → `tenants.id` — each business has its own rules
- add index on `tenant_id`

**`employees`**
- add `tenant_id` int NOT NULL, FK → `tenants.id`
- becomes a per-business roster of people whose expenses are tracked, decoupled
  from login. Kept for Phase 2 anomaly stats. `email` uniqueness changes from
  global to `UNIQUE (tenant_id, email)`.
- add index on `tenant_id`

**`fraud_flags`**
- no change. Tenant scope is inherited through `receipt_id`.

### Migration

The database has no production data. The Alembic migration adds the new tables
and `ALTER`s the existing ones with `tenant_id` / `submitted_by_user_id` as
NOT NULL with no backfill (any existing dev rows can be truncated). Update the
SQLAlchemy models first, then autogenerate and hand-check the migration.

Alembic connects on the **direct** connection (port 5432). The app runtime uses
the **pooled** connection (port 6543).

## Auth & request scoping

### Login (frontend ↔ Supabase, no FastAPI)

Frontend uses `supabase-js`: `signUp` / `signInWithPassword` / `resetPasswordForEmail`.
Supabase returns an access token (JWT, ~1h TTL) and a refresh token. The frontend
stores them and sends `Authorization: Bearer <access_token>` on every FastAPI
request. Token refresh is handled by `supabase-js` on the client.

### JWT verification (FastAPI)

New module `app/auth.py`:
- Verifies the JWT signature with `SUPABASE_JWT_SECRET` (symmetric, HS256) using
  `python-jose`. No network call to Supabase per request.
- Validates `exp` and `aud` (`authenticated`).
- Extracts `sub` (user uuid) and `email`.

### Active tenant resolution

- The client sends `X-Tenant-ID: <tenant_id>` on tenant-scoped requests.
- Dependency `get_current_context(...)`:
  1. verify JWT → `user_id`, `email`
  2. read `X-Tenant-ID`; missing on a tenant-scoped route → `400`
  3. look up `memberships` for `(user_id, tenant_id)`; missing → `403`
  4. return `RequestContext(user_id: UUID, email: str, tenant_id: int, role: str)`
- Every tenant-scoped endpoint depends on `get_current_context` and uses
  `context.tenant_id` for all filters and inserts. Client-supplied tenant/user
  fields in request bodies are ignored.

### New settings / `.env.example`

| Key | Purpose |
|---|---|
| `SUPABASE_URL` | project URL, for storage + service-role calls |
| `SUPABASE_JWT_SECRET` | verify access tokens |
| `SUPABASE_SERVICE_ROLE_KEY` | server-only; look up a user by email during add-member |
| `SUPABASE_STORAGE_BUCKET` | default `receipts` |
| `DATABASE_URL` | pooled connection (`:6543`), app runtime |
| `DATABASE_URL_DIRECT` | direct connection (`:5432`), Alembic |

### New dependencies

`python-jose[cryptography]`, `httpx`, `supabase` (or `storage3`) for the Storage
client.

## API surface

### New router `app/api/v1/auth.py`

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/auth/me` | current user (`user_id`, `email`) plus their memberships (tenant id, name, role). Frontend calls this after login to populate the business switcher. |

Signup / login / password reset are **not** FastAPI endpoints.

### New router `app/api/v1/tenants.py`

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/tenants` | any authed user | create a business; caller gets an `owner` membership |
| `GET` | `/api/v1/tenants` | authed user | list businesses the caller belongs to |
| `GET` | `/api/v1/tenants/{id}/members` | member of `{id}` | list members |
| `POST` | `/api/v1/tenants/{id}/members` | member of `{id}` | add a user by email; the email must already have a Supabase account (resolved to uuid via a service-role call); inserts a `member` membership; `404` if no such account |
| `DELETE` | `/api/v1/tenants/{id}/members/{user_id}` | member of `{id}` | remove a membership |

`{id}` in these routes is the path tenant; `get_current_context` still requires
`X-Tenant-ID` to match, so a caller cannot operate on a tenant they did not
select. Membership in the target tenant is required.

### Changed router `app/api/v1/receipts.py`

Every endpoint gains `context: RequestContext = Depends(get_current_context)`.

- `POST /upload`
  - sets `tenant_id` and `submitted_by_user_id` from `context`; ignores any
    client-supplied values
  - stores the file in Supabase Storage at
    `{bucket}/{tenant_id}/{uuid}.{ext}` instead of the local filesystem;
    `receipts.image_path` holds the object path
  - order: upload object → insert row; on insert failure, best-effort delete the
    object
- `GET /{receipt_id}` — `404` if the row's `tenant_id` != `context.tenant_id`
  (do not distinguish "not found" from "belongs to another tenant")
- `GET /` (list) — always filtered by `context.tenant_id`

### Unchanged

`GET /health`.

## Error handling

| Situation | Response |
|---|---|
| Missing / malformed / expired JWT | `401`, consistent JSON error shape via a shared handler |
| Valid JWT, no membership for requested tenant | `403` |
| Missing `X-Tenant-ID` on a tenant-scoped route | `400` with a clear message |
| Access to a resource in another tenant | `404` (never `403`) |
| Add-member email has no Supabase account | `404` "no account for that email; ask them to sign up first" |
| Supabase Storage upload failure | `502`; receipt row not created |

## Testing (pytest)

`conftest.py` provides:
- a helper that mints a valid HS256 JWT signed with a test `SUPABASE_JWT_SECRET`
- fixtures seeding two tenants, each with a user and a membership
- mocks for the Supabase service-role email lookup and the Storage client so
  tests run offline

Test cases:
- **Auth:** no token → 401; expired token → 401; valid token but wrong/absent
  `X-Tenant-ID` → 400/403
- **Isolation:** user A cannot `GET`, list, or otherwise reach tenant B's
  receipts; list endpoints only return the active tenant's rows
- **Tenants:** `POST /tenants` creates an `owner` membership; add member with an
  unknown email → 404; add member with a known email → membership row created
- **Receipts:** upload persists `tenant_id` + `submitted_by_user_id` from context
  and ignores body-supplied values

## Implementation sequence

1. Branch, settings/config additions, `.env.example`, new dependencies
2. `app/auth.py` — JWT verification, `RequestContext`, `get_current_context`,
   shared 401/403 handlers
3. Models: add `Tenant`, `Membership`; add `tenant_id` /
   `submitted_by_user_id` to existing models; Alembic migration
4. Routers: `app/api/v1/auth.py` (`/me`), `app/api/v1/tenants.py`
5. Retrofit `app/api/v1/receipts.py` with `get_current_context` scoping
6. Swap local file storage for Supabase Storage in `POST /upload`
7. Tests: auth, isolation, tenant CRUD, receipt scoping
8. Point Alembic (`DATABASE_URL_DIRECT`) and runtime (`DATABASE_URL`) at
   Supabase, run the migration, manual smoke test through the running API

## Out of scope (Phase 2)

- Row-Level Security policies and per-request `SET LOCAL app.current_tenant`
- Role enforcement (admin vs member permissions)
- Invite tokens and invitation emails
- The fraud detection pipeline (OCR, duplicate detection, policy validator,
  scorer) and the `/analyze` endpoint
- Frontend application
- `supabase/migrations/*.sql` for Storage bucket + RLS policy definitions
