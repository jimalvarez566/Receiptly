import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JOSEError, jwt
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

# Guards against hammering Supabase with a refetch on every request that
# carries an unknown/bogus `kid` (e.g. a self-signed token from an attacker).
_JWKS_REFETCH_COOLDOWN_SECONDS = 10.0
_jwks_last_fetch_attempt: float = 0.0


@dataclass
class CurrentUser:
    user_id: UUID
    email: str | None


def _unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _filter_signing_keys(keys: list[dict]) -> dict[str, dict]:
    """Keep only EC signing keys, keyed by kid.

    Supabase JWKS can carry non-EC or non-signing entries (e.g. during key
    rotation). A non-EC key handed to an ES256 decode raises JWKError rather
    than JWTError, so such entries must never reach that path.
    """
    return {
        k["kid"]: k
        for k in keys
        if k.get("kid") and k.get("kty") == "EC" and k.get("use", "sig") == "sig"
    }


async def _fetch_jwks() -> dict[str, dict]:
    """Fetch the project's JWKS. Returns {kid: jwk} for EC signing keys only.
    Raises on network/parse error."""
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return _filter_signing_keys(resp.json().get("keys", []))


async def _get_signing_key(kid: str) -> dict:
    """Return the JWK for `kid`, refetching the JWKS once on a cache miss."""
    global _jwks_last_fetch_attempt

    cached = _jwks_cache.get(kid)
    if cached is not None:
        return cached
    async with _jwks_lock:
        cached = _jwks_cache.get(kid)
        if cached is not None:
            return cached
        now = time.monotonic()
        if now - _jwks_last_fetch_attempt < _JWKS_REFETCH_COOLDOWN_SECONDS:
            # A refetch already happened (or was attempted) recently and this
            # kid still wasn't found — don't hit Supabase again for every
            # request carrying an unknown kid.
            raise _unauthorized()
        _jwks_last_fetch_attempt = now
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
    except JOSEError as exc:
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
    except JOSEError as exc:
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
    if x_tenant_id is None:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")
    try:
        tenant_id = int(x_tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="X-Tenant-ID must be an integer"
        ) from exc

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=403, detail="Not a member of the requested business"
        )

    return RequestContext(
        user_id=user.user_id,
        email=user.email,
        tenant_id=tenant_id,
        role=membership.role,
    )
