# ES256 / JWKS Token Verification — Design

**Date:** 2026-09-10
**Branch:** `feature/supabase-multitenant-auth` (continues on the same branch)
**Status:** Approved for implementation planning

## Problem

The Supabase project (`kxbavxzlrxxzvnhnxbku`) uses **asymmetric JWT signing
keys**: GoTrue issues user session tokens signed with **ES256**, and publishes
the verifying public keys at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`.

`app/auth.py._decode` only verifies **HS256** with the shared
`SUPABASE_JWT_SECRET`:

```python
jwt.decode(token, settings.SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
```

An ES256 token can never verify this way, so `jwt.decode` raises `JWTError` and
every authenticated request returns `401 "Invalid or expired token"`. The
frontend then signs out and redirects to `/login` (correct 401 behavior), which
looks like an infinite login loop.

Confirmed empirically against the running backend:

| Token | `GET /api/v1/auth/me` |
|---|---|
| HS256, signed with `SUPABASE_JWT_SECRET` (what `conftest.make_token` produces) | 200 |
| Real ES256 session token from `supabase.auth.signInWithPassword` | 401 |

The 100-test suite passes because every test token is HS256.

## Goal

`get_current_user` accepts **both**:
- HS256 tokens signed with `SUPABASE_JWT_SECRET` (test suite; legacy static keys).
- ES256 tokens signed by the project's current signing key, verified against the
  JWKS.

Real Supabase logins work end to end. The existing HS256 tests keep passing
unchanged.

## Non-goals

- Switching the app to ES256-only (would require rebuilding the test-token
  infrastructure for no benefit).
- `iss` claim validation (HS256 test tokens don't set it; signature + `aud` is
  sufficient for this phase).
- JWKS TTL timers / background refresh — an in-memory cache plus a one-shot
  refetch on cache miss covers key rotation.
- Any change to `get_current_context`, the tenant model, or the frontend.

## Design

### `app/auth.py`

**JWKS cache (module level):**

```python
_jwks_cache: dict[str, dict] = {}   # kid -> JWK dict
_jwks_lock = asyncio.Lock()
```

**`async def _fetch_jwks() -> dict[str, dict]`** — the network seam (tests
monkeypatch this): GET
`f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"` with
`httpx.AsyncClient(timeout=5.0)`, return `{k["kid"]: k for k in
body["keys"] if "kid" in k}`. Network/HTTP/parse errors propagate to the caller.

**`async def _get_signing_key(kid: str) -> dict`:**
1. If `kid` in `_jwks_cache`, return it.
2. Under `_jwks_lock` (re-check the cache after acquiring, to avoid a stampede):
   `_jwks_cache = await _fetch_jwks()` — wrapped in try/except; on any exception
   log at `warning` and raise `HTTPException(401, "Invalid or expired token")`.
3. If `kid` now in `_jwks_cache`, return it; else raise `HTTPException(401,
   "Invalid or expired token")`.

**`async def _decode(token: str) -> dict`** (was sync):
1. `header = jwt.get_unverified_header(token)` inside a try — malformed → 401.
2. `alg = header.get("alg")`. If `alg not in {"HS256", "ES256"}` → 401. (Rejects
   `none` and algorithm-confusion attempts.)
3. `alg == "HS256"`:
   ```python
   jwt.decode(token, settings.SUPABASE_JWT_SECRET,
              algorithms=["HS256"], audience="authenticated")
   ```
4. `alg == "ES256"`:
   ```python
   kid = header.get("kid")
   if not kid: raise HTTPException(401, "Invalid or expired token")
   jwk = await _get_signing_key(kid)
   jwt.decode(token, jwk, algorithms=["ES256"], audience="authenticated")
   ```
5. Wrap the `jwt.decode` calls: `JWTError` → `HTTPException(401, "Invalid or
   expired token")`.

The verification key and `algorithms=[…]` are always chosen strictly from the
header's `alg` — a public key is never passed where an HS secret is expected, so
there is no alg-confusion exposure.

**`get_current_user`:** unchanged except `payload = await _decode(creds.credentials)`.

**Imports added:** `asyncio`, `logging`, `httpx`. A module `logger =
logging.getLogger(__name__)`.

### `app/config.py`

No change. The JWKS URL is derived from `SUPABASE_URL` at call time.

### Tests — `tests/test_auth_es256.py` (new)

Helpers (module level):
- Generate a P-256 keypair once: `ec.generate_private_key(ec.SECP256R1())`
  (`from cryptography.hazmat.primitives.asymmetric import ec` — `cryptography` is
  already installed via `python-jose[cryptography]`).
- `_TEST_KID = "test-es256-key"`.
- Build the JWK for the **public** key (via
  `jose.backends.cryptography_backend.CryptographyECKey(pub, "ES256").to_dict()`,
  then add `"kid": _TEST_KID`). Exact construction is an implementation detail
  for the plan; the requirement is a valid ES256 JWKS entry.
- `make_es256_token(sub, *, kid=_TEST_KID, exp_delta=3600, aud="authenticated")`
  — signs a payload with the private key using `python-jose`
  (`jwt.encode(payload, private_key_pem, algorithm="ES256", headers={"kid": kid})`).

Fixture: `autouse` fixture that, after each test, resets `app.auth._jwks_cache`
to `{}` (so tests don't leak keys into each other). Tests that need the key
"already cached" seed `app.auth._jwks_cache[_TEST_KID] = <public jwk>` directly.
Tests that exercise the refetch path `monkeypatch.setattr(app.auth,
"_fetch_jwks", <async stub returning the desired kid->jwk dict>)`. No test hits
the network.

Cases:
1. **Valid ES256 token verifies.** Drive a probe FastAPI app with a
   `get_current_user`-dependent route (same pattern as `test_auth.py`), send
   `Authorization: Bearer <es256 token>` → 200, `user_id` == `sub`.
2. **Unknown `kid` → 401.** Token with `kid="nope"`, cache seeded only with
   `_TEST_KID`, and the refetch returns a JWKS that also lacks it → 401.
3. **`alg: "none"` → 401.** A token with `{"alg": "none"}` header and no
   signature → 401.
4. **Expired ES256 token → 401.** `exp_delta=-60`.
5. **HS256 still works** (regression): reuse `conftest.make_token` through the
   same probe route → 200.

Existing `tests/test_auth.py` (HS256 paths, missing/expired/malformed) must pass
unchanged. `conftest.py` is not modified.

### Manual verification (after implementation)

With the backend pointed at Supabase and the frontend running:
- Sign up / log in in the browser → land on "Create your first business" (no
  401 loop) → create a business → the app loads.
- `curl` a real ES256 token (obtained via `supabase.auth.signInWithPassword` in
  the browser console) at `GET /api/v1/auth/me` → 200.

## Rollout notes

- No new dependency (`httpx`, `cryptography` both already present).
- No config or migration change.
- First authenticated request after a deploy does one JWKS fetch (~1 round trip),
  then it's cached for the process lifetime; a token with an unseen `kid`
  triggers exactly one refetch.
- If Supabase later rotates to a new `kid`, the next request with that `kid`
  refetches and succeeds automatically.
