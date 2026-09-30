# ES256 / JWKS Token Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `get_current_user` verifies both HS256 tokens (signed with `SUPABASE_JWT_SECRET`, used by the test suite and legacy keys) and ES256 tokens (real Supabase user sessions, verified against the project JWKS).

**Architecture:** `_decode` branches on the token header's `alg`. HS256 → verify with the shared secret (unchanged). ES256 → look up the signing key by `kid` in an in-memory JWKS cache, refetching `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` once on a cache miss. `_decode` and `get_current_user` become async (the latter already is).

**Tech Stack:** FastAPI, `python-jose[cryptography]` 3.4.0 (already installed — provides `jose.jwt` + `jose.backends.cryptography_backend` + pulls in `cryptography`), `httpx` 0.28.1 (already a dep), pytest + pytest-asyncio. Local Postgres `fraud_detection_test`.

---

## Reference

Spec: `docs/superpowers/specs/2026-09-10-es256-jwks-token-verification-design.md` — read it first.

## Context the engineer needs

- Branch: `feature/supabase-multitenant-auth`. Stay on it. Never touch `main`.
- Tests: `cd backend && python3 -m pytest` (use `python3`). Current: **100 passing**.
- The bug: real Supabase user tokens are ES256 (`{"alg":"ES256","kid":"..."}`), verified via a JWKS endpoint. `app/auth.py._decode` only does HS256 with `settings.SUPABASE_JWT_SECRET`, so every real token → `JWTError` → 401. Every existing test passes because `tests/conftest.py:make_token` mints HS256.
- `_decode` is currently **sync** and called only from `get_current_user` (which is `async`). No test calls `_decode` directly (grep-verified: `tests/test_auth.py` imports only `CurrentUser`, `get_current_user`, `RequestContext`, `get_current_context`).
- Verified working mechanics for the ES256 test path (prototyped):
  - `CryptographyECKey(private_key.public_key(), "ES256").to_dict()` → a JWK dict; add `["kid"]`.
  - `jwt.encode(payload, private_key_pem_str, algorithm="ES256", headers={"kid": kid})` → an ES256 token.
  - `jwt.decode(token, jwk_dict, algorithms=["ES256"], audience="authenticated")` → verifies against the JWK dict directly.
  - `jwt.get_unverified_header(token)` on an `alg:none` token returns `{"alg":"none","typ":"JWT"}`.

### Current `backend/app/auth.py` (FULL — Task 2 rewrites the top portion)

```python
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Membership

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: UUID
    email: str | None


def _decode(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    payload = _decode(creds.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    return CurrentUser(user_id=UUID(sub), email=payload.get("email"))


@dataclass
class RequestContext:
    user_id: UUID
    email: str | None
    tenant_id: int
    role: str


async def get_current_context(
    user: CurrentUser = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db),
) -> RequestContext:
    ...  # UNCHANGED — do not touch this function
```

## File Structure

| Path | Change |
|---|---|
| `backend/tests/test_auth_es256.py` | **new** — ES256 verification tests + EC-key helpers |
| `backend/app/auth.py` | JWKS cache + `_fetch_jwks` + `_get_signing_key` + async alg-branching `_decode`; `get_current_user` awaits `_decode` |

No config, migration, or conftest change.

---

## Task 1: Write the failing ES256 tests

**Files:**
- Create: `backend/tests/test_auth_es256.py`

- [ ] **Step 1: Write the test file**

Create `backend/tests/test_auth_es256.py`:

```python
import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from jose.backends.cryptography_backend import CryptographyECKey

import app.auth as auth_mod
from app.auth import CurrentUser, get_current_user

_TEST_KID = "test-es256-key"

_priv = ec.generate_private_key(ec.SECP256R1())
_PRIV_PEM = _priv.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUB_JWK = CryptographyECKey(_priv.public_key(), "ES256").to_dict()
_PUB_JWK["kid"] = _TEST_KID


def make_es256_token(
    sub: str,
    *,
    kid: str = _TEST_KID,
    exp_delta: int = 3600,
    aud: str = "authenticated",
    email: str = "es@example.com",
) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "email": email, "aud": aud, "exp": now + exp_delta, "iat": now},
        _PRIV_PEM,
        algorithm="ES256",
        headers={"kid": kid},
    )


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth_mod._jwks_cache.clear()
    yield
    auth_mod._jwks_cache.clear()


@pytest.fixture
def probe_client():
    probe = FastAPI()

    @probe.get("/whoami")
    async def whoami(user: CurrentUser = Depends(get_current_user)):
        return {"user_id": str(user.user_id), "email": user.email}

    transport = ASGITransport(app=probe)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_valid_es256_token_verifies_from_cache(probe_client):
    uid = str(uuid.uuid4())
    auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK
    token = make_es256_token(uid)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid


async def test_valid_es256_token_triggers_one_jwks_fetch(probe_client, monkeypatch):
    uid = str(uuid.uuid4())
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return {_TEST_KID: _PUB_JWK}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
    token = make_es256_token(uid)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert calls["n"] == 1


async def test_unknown_kid_is_401(probe_client, monkeypatch):
    async def fake_fetch():
        return {"another-kid": _PUB_JWK}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
    token = make_es256_token(str(uuid.uuid4()), kid="missing-kid")
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_alg_none_is_401(probe_client):
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"sub": "x", "aud": "authenticated"}) + "."
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_expired_es256_token_is_401(probe_client):
    auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK
    token = make_es256_token(str(uuid.uuid4()), exp_delta=-60)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_jwks_fetch_failure_is_401(probe_client, monkeypatch):
    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(auth_mod, "_fetch_jwks", boom)
    token = make_es256_token(str(uuid.uuid4()))
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_hs256_token_still_verifies(probe_client):
    from tests.conftest import make_token

    uid = str(uuid.uuid4())
    token = make_token(uid, "hs@example.com")
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid
```

- [ ] **Step 2: Run — verify it fails**

Run: `cd backend && python3 -m pytest tests/test_auth_es256.py -v`
Expected: FAIL at collection or in every ES256 test — `AttributeError: module 'app.auth' has no attribute '_jwks_cache'` (and `_fetch_jwks`). `test_alg_none_is_401` may currently pass by accident (HS256 decode of an `alg:none` token also 401s) and `test_hs256_token_still_verifies` currently passes — that's fine; the point is the ES256 cases fail.

- [ ] **Step 3: Commit the failing test**

```bash
cd backend && git add tests/test_auth_es256.py
git commit -m "test: add failing ES256/JWKS token verification tests"
```
(Use `git -C ..` + `backend/`-prefixed path if git complains about the repo root being the parent.)

---

## Task 2: Implement ES256 + JWKS verification in `app/auth.py`

**Files:**
- Modify: `backend/app/auth.py`

- [ ] **Step 1: Replace the top of the file (imports through `get_current_user`)**

In `backend/app/auth.py`, replace everything from the first line **down to and including** the `get_current_user` function (i.e. the block ending just before `@dataclass\nclass RequestContext:`) with:

```python
import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Membership

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

_ALLOWED_ALGS = {"HS256", "ES256"}

# kid -> JWK dict. Populated lazily from the Supabase JWKS endpoint, refetched
# whole on a cache miss (handles key rotation). Lives for the process lifetime.
_jwks_cache: dict[str, dict] = {}
_jwks_lock = asyncio.Lock()


@dataclass
class CurrentUser:
    user_id: UUID
    email: str | None


def _unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _fetch_jwks() -> dict[str, dict]:
    """Fetch the project's JWKS. Returns {kid: jwk}. Raises on network/parse error."""
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {k["kid"]: k for k in keys if "kid" in k}


async def _get_signing_key(kid: str) -> dict:
    """Return the JWK for `kid`, refetching the JWKS once on a cache miss."""
    cached = _jwks_cache.get(kid)
    if cached is not None:
        return cached
    async with _jwks_lock:
        cached = _jwks_cache.get(kid)
        if cached is not None:
            return cached
        try:
            fresh = await _fetch_jwks()
        except Exception as exc:  # noqa: BLE001 — any fetch/parse failure -> 401
            logger.warning("Failed to fetch Supabase JWKS: %s", exc)
            raise _unauthorized() from exc
        _jwks_cache.clear()
        _jwks_cache.update(fresh)
    key = _jwks_cache.get(kid)
    if key is None:
        raise _unauthorized()
    return key


async def _decode(token: str) -> dict:
    """Verify a Supabase JWT (HS256 with the shared secret, or ES256 via JWKS)."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise _unauthorized() from exc

    alg = header.get("alg")
    if alg not in _ALLOWED_ALGS:
        raise _unauthorized()

    try:
        if alg == "HS256":
            return jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        kid = header.get("kid")
        if not kid:
            raise _unauthorized()
        jwk = await _get_signing_key(kid)
        return jwt.decode(
            token,
            jwk,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise _unauthorized() from exc


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None:
        raise _unauthorized("Missing bearer token")
    payload = await _decode(creds.credentials)
    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Token missing subject")
    return CurrentUser(user_id=UUID(sub), email=payload.get("email"))
```

Leave `RequestContext` and `get_current_context` (the rest of the file) exactly as they are.

- [ ] **Step 2: Run the ES256 tests — verify they pass**

Run: `cd backend && python3 -m pytest tests/test_auth_es256.py -v`
Expected: 7 passed.

- [ ] **Step 3: Run the full suite — no regressions**

Run: `cd backend && python3 -m pytest -q`
Expected: **107 passed** (100 + 7). In particular `tests/test_auth.py` (HS256 missing/malformed/expired, `get_current_context` cases), `tests/test_auth_me.py`, `tests/test_tenants.py`, `tests/test_receipts_scoping.py` all still pass — the HS256 path is unchanged and `conftest.make_token` is untouched.

- [ ] **Step 4: Sanity-check the async change didn't leave a sync caller**

Run: `cd backend && grep -rn "_decode(" app/ tests/`
Expected: the only call is `await _decode(creds.credentials)` inside `get_current_user`. No other file calls `_decode`.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/auth.py
git commit -m "feat: verify ES256 Supabase tokens via JWKS, keep HS256 support"
```

---

## Task 3: Manual verification against real Supabase + docs

**Files:** none, then `README.md` / `TODO.md`

Prerequisites: `backend/.env` points at Supabase (already set), `frontend/.env` has a real `VITE_SUPABASE_ANON_KEY` (already set).

- [ ] **Step 1: Start both servers**

```bash
cd backend && uvicorn app.main:app --port 8000
```
```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Direct token check**

In the browser dev console at the frontend origin:
```js
const { supabase } = await import('/src/lib/supabase.ts')
const si = await supabase.auth.signInWithPassword({ email: 'smoke3@example.com', password: 'smoketest123' })
const tok = si.data.session.access_token
const r = await fetch('http://localhost:8000/api/v1/auth/me', { headers: { Authorization: 'Bearer ' + tok } })
console.log(r.status, await r.text())
```
Expected: **200** with `{"user_id": "...", "email": "smoke3@example.com", "memberships": [...]}`.
(`smoke3@example.com` / `smoketest123` was created during the earlier verification; if it's gone, sign up a fresh one first.)

- [ ] **Step 3: Full browser flow**

Open the frontend, go to `/signup`, register a fresh email + password (≥6 chars). Expect: **no login loop** — you land on "Create your first business". Create one → the app loads on the Dashboard. Upload a receipt → it appears in the list, opens, and Approve works.

- [ ] **Step 4: Stop the servers**

```bash
pkill -f "uvicorn app.main:app"; pkill -f vite
```

- [ ] **Step 5: Update docs**

In `TODO.md`, under `### Supabase Auth & Multi-Tenancy`, add:
```markdown
- [x] Backend verifies both HS256 and ES256 (JWKS) Supabase tokens — real user sessions work end to end
```

In the repo-root `README.md`, in the `### Auth & multi-tenancy` section, add a bullet:
```markdown
- The backend accepts both HS256 tokens (shared JWT secret) and ES256 tokens
  (verified against the project's JWKS at `/auth/v1/.well-known/jwks.json`,
  cached in memory). Supabase projects using asymmetric signing keys work
  without extra configuration.
```

- [ ] **Step 6: Commit**

```bash
cd /Users/syonchau/ai_fraud_payroll && git add README.md TODO.md
git commit -m "docs: note ES256/JWKS token support"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| `_jwks_cache` + `_jwks_lock` module state | Task 2 Step 1 |
| `_fetch_jwks` (network seam, tests monkeypatch it) | Task 2 Step 1; monkeypatched in Task 1 tests 2/3/6 |
| `_get_signing_key` — cache hit, double-checked-locked refetch, miss → 401, fetch error → 401+warning | Task 2 Step 1; Task 1 tests `unknown_kid`, `jwks_fetch_failure`, `triggers_one_jwks_fetch` |
| `_decode` async, `alg` allowlist `{HS256, ES256}`, `none`/other → 401 | Task 2 Step 1; Task 1 test `alg_none_is_401` |
| HS256 path unchanged (secret + `aud`) | Task 2 Step 1; Task 1 test `hs256_token_still_verifies` + full suite in Step 3 |
| ES256 path: `kid` required, JWK lookup, `algorithms=["ES256"]`, `aud` | Task 2 Step 1; Task 1 tests `verifies_from_cache`, `expired_es256` |
| `get_current_user` awaits `_decode`, keeps "Missing bearer token" / "Token missing subject" | Task 2 Step 1 |
| No config / migration / conftest change | none touched |
| New `tests/test_auth_es256.py` with the listed cases | Task 1 |
| Manual verification (browser sign-in, no 401 loop; curl real ES256 → 200) | Task 3 |
| Rollout notes (README/TODO) | Task 3 Step 5 |

No gaps.

**Placeholder scan:** No "TBD" / vague steps. Every code step is a complete file or an exact replacement block; every run step has a command and expected result. The one judgement call ("replace from line 1 down to and including `get_current_user`") is bounded by a quoted anchor (`@dataclass\nclass RequestContext:`).

**Type / name consistency:**
- `_fetch_jwks` — defined Task 2, monkeypatched by name in Task 1 (`monkeypatch.setattr(auth_mod, "_fetch_jwks", ...)`), signature `() -> dict[str, dict]` in both.
- `_jwks_cache` — defined Task 2, seeded directly in Task 1 (`auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK`) and cleared by the autouse fixture.
- `_get_signing_key(kid: str) -> dict` — defined Task 2, exercised transitively via `_decode`.
- `_decode` — async in Task 2, `await`ed in `get_current_user` (same task); grep check in Task 2 Step 4 confirms no stale sync caller.
- `_unauthorized(detail=...)` — helper defined Task 2, used for every 401 in the rewritten block; replaces the inline `HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, ...)` calls. `test_auth.py` asserts only `status_code == 401`, so the message change is safe.
- `CurrentUser` / `get_current_user` — names unchanged; `test_auth_es256.py` imports both from `app.auth`.
- `make_es256_token` / `_PUB_JWK` / `_TEST_KID` — defined in `test_auth_es256.py` (Task 1), used only there.
