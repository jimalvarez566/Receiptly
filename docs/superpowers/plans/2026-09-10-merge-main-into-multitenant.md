# Merging `main` into `feature/supabase-multitenant-auth` — Resolution Plan

**Date:** 2026-09-10
**Status:** Not yet executed. This documents how every conflict and semantic
clash will be resolved when we do merge, so the merge is mechanical when the time
comes.

`main` advanced from `148064a` to `91ede62` (6 commits): Tesseract OCR, the MVP
fraud pipeline (duplicate / policy / scoring), a Gemini explainer endpoint, and a
React frontend. Our branch adds Supabase auth + multi-tenancy. A trial
`git merge main` produces **8 conflicted files** plus one semantic issue Git does
not flag (duplicate Alembic head).

## Conflict-by-conflict resolution

### 1. Alembic — duplicate head (NOT shown as a conflict)

`main`'s `6d34720ba4d7_add_explanation_column.py` and our
`a1b2c3d4e5f6_multi_tenant.py` both set `down_revision = "210bbc94ba38"`. After
the merge `alembic heads` would list two heads.

**Fix:** in `a1b2c3d4e5f6_multi_tenant.py` change
`down_revision = "210bbc94ba38"` → `down_revision = "6d34720ba4d7"`. History
becomes linear: `210bbc94ba38 → 6d34720ba4d7 → a1b2c3d4e5f6`. Our migration does
not touch `explanation`, so nothing else changes. Safe because our migration has
only ever been applied to a throwaway test DB.
**Verify:** `alembic upgrade head` then `alembic check` → "No new upgrade
operations detected."

### 2. `backend/app/models/receipt.py` — auto-merges clean

`main` adds `explanation` (Text, nullable); we add `tenant_id` +
`submitted_by_user_id`. Different lines, no manual action. Confirm the merged file
has all three.

### 3. `backend/app/schemas/receipt.py` — take main's, re-apply our 2 fields

`main` adds `explanation: str | None` to `ReceiptResponse`, plus `ReviewRequest`
and `ReviewResponse(ReceiptResponse)`, and switches an import to
`from typing import Literal`.

**Fix:** keep main's version, then:
- keep `from uuid import UUID` **and** `from typing import Literal`
- re-insert into `ReceiptResponse`, right after `id: int`:
  ```python
      tenant_id: int
      submitted_by_user_id: UUID
  ```
`ReviewResponse` inherits from `ReceiptResponse`, so it will also require those
two fields — fine, a reviewed receipt always has them.

### 4. `backend/pytest.ini` — union

```ini
[pytest]
pythonpath = .
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
```

### 5. `backend/requirements.txt` — union of both dependency sets

Keep everything from `main` (pytesseract, Pillow, pdf2image, imagehash, slowapi,
google-generativeai) and add ours:
```
python-jose[cryptography]==3.4.0
httpx==0.28.1
pydantic[email]==2.10.3
pytest==8.3.4
pytest-asyncio==0.25.0
```
(`main`'s list has no `pytest` pin; ours supplies it.)

### 6. `backend/app/main.py` — combine limiter wiring + our routers

```python
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import auth_routes, receipts, tenants
from app.api.v1.receipts import limiter
from app.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Expense Fraud Detection API",
    description="AI-powered system for detecting fraudulent expense reports",
    version="0.1.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(receipts.router, prefix="/api/v1")
app.include_router(tenants.router, prefix="/api/v1")
app.include_router(auth_routes.router, prefix="/api/v1")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

### 7. `backend/tests/conftest.py` — concatenate (fixtures are complementary)

`main`'s conftest defines sync ORM-factory fixtures for the service unit tests:
`make_receipt`, `make_rule`, `make_flag` (no DB, no async). Ours defines the
async harness: the env-var guard, `test_engine` (NullPool), `db_session`,
`client`, `make_token`, `user_a_id`, `user_b_id`.

**Fix:** one file — our module-level env setup and imports at the top (unchanged),
then our async fixtures, then paste `main`'s three factory fixtures verbatim. No
name collisions.

**Caveat:** after the merge, importing `app.main` pulls in `app.api.v1.receipts`,
which imports `app.services.ocr` → `pytesseract`, `app.services.duplicate_detector`
→ `imagehash`/`PIL`, and `google.generativeai`. Our API tests import `app.main`,
so **the test environment must have main's OCR/pipeline deps installed** or
collection fails. `pip install -r requirements.txt` after the merge covers it.
Tesseract itself is only invoked at request time (and we mock it in tests), so the
binary is not required for the suite.

### 8. `backend/app/api/v1/receipts.py` — the substantive merge

Start from **main's version** (pipeline + `/analyze` + `/explain` + `/review`),
then layer our cross-cutting concerns.

**Imports to add back:**
```python
from app import supabase_client
from app.auth import RequestContext, get_current_context
```
and restore the `CONTENT_TYPES` map (`.png/.jpg/.jpeg/.pdf → mime`).

**`upload_receipt`:**
- Signature: drop `employee_id: int | None = None`, add
  `context: RequestContext = Depends(get_current_context)`.
- Storage: `main` writes the upload to local disk and then feeds that path to
  `extract_receipt_data(path)` and `compute_image_hash(path)`. Our design stores
  in Supabase Storage, so there is no local path. **Resolution:** write the bytes
  to a `tempfile.NamedTemporaryFile(suffix=ext, delete=False)`, run OCR + hash on
  the temp path, upload the bytes to Supabase Storage at
  `f"{context.tenant_id}/{uuid4().hex}{ext}"`, then delete the temp file in a
  `finally`. `image_path` stores the Storage object path (our convention).
  Keep our 502-on-upload-failure and best-effort orphan cleanup on DB flush
  failure.
- `Receipt(...)` gains `tenant_id=context.tenant_id,
  submitted_by_user_id=context.user_id`.
- Keep `main`'s post-OCR field assignments and the `await run_fraud_pipeline(
  receipt, db)` call unchanged.

**`analyze_receipt`, `explain_receipt`, `review_receipt`:** add
`context: RequestContext = Depends(get_current_context)` to each, and add
`Receipt.tenant_id == context.tenant_id` to every `select(Receipt)…where(…)` so a
cross-tenant id returns 404. In `review_receipt`, the "earliest receipt with the
same image hash" lookup must also filter `Receipt.tenant_id == context.tenant_id`
(a duplicate chain never crosses businesses).

**`get_receipt`, `list_receipts`:** use our tenant-scoped versions as already
committed on this branch.

**`limiter`** stays exactly as `main` has it.

### 9. `backend/tests/test_receipts_scoping.py` — extend the mock fixture

`upload` now runs OCR + pipeline, so the autouse fixture that currently patches
`supabase_client.upload_object`/`delete_object` must also patch, on the
`app.api.v1.receipts` module: `extract_receipt_data` (return a stub OCR result
object), `compute_image_hash` (return a fixed string), and `run_fraud_pipeline`
(no-op async). Otherwise the upload tests will try to shell out to Tesseract.

### 10. Docs — union, mechanical

- `README.md`, `TODO.md`: take `main`'s as base, re-apply our additions (the
  "Auth & multi-tenancy" README subsection + the 6 endpoint rows + the Environment
  subsection; the TODO "Next (Phase 2 hardening of multi-tenancy)" section + the
  Done line).
- `IMPLEMENTATION.md`, `PROJECT.md`: `main`-only edits, keep `main`'s.
- `API.md`, `FRONTEND.md`, `GEMINI_EXPLAINER.md`: new on `main`, keep. After the
  merge, add the auth/tenant + `/analyze`/`/explain`/`/review`-now-protected notes
  to `API.md`.

## Out of scope for the merge itself — separate follow-up

**Frontend auth.** `frontend/src/lib/api.ts` sends no `Authorization` or
`X-Tenant-ID`. Once auth lands, every call 401s until the frontend gets: a
Supabase login screen (`supabase-js`), storage of the access token, an
`Authorization: Bearer` + `X-Tenant-ID` on each request, and a
business switcher backed by `GET /api/v1/auth/me`. Track as its own task.

## Post-merge verification checklist

- [ ] `cd backend && alembic upgrade head` — linear, no "multiple heads"
- [ ] `cd backend && alembic check` — clean
- [ ] `cd backend && pip install -r requirements.txt` — resolves
- [ ] `cd backend && pytest` — our API suite (33) **and** main's
      `tests/test_services/` suite both green
- [ ] `/analyze`, `/explain`, `/review` now reject calls without a valid JWT
      (401) and without `X-Tenant-ID` (400), and 404 on a cross-tenant receipt id
- [ ] `upload` still produces OCR fields + fraud flags, now with `tenant_id` /
      `submitted_by_user_id` set and the image in Supabase Storage
