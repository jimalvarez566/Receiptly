"""Thin async wrappers over the Supabase REST API (GoTrue admin + Storage).

Every network call goes through the module-level ``_client()`` factory so tests
can monkeypatch it with a ``MockTransport``-backed ``httpx.AsyncClient``.
"""

import logging
from urllib.parse import quote
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# GoTrue's admin user list is paginated and ignores any email filter, so lookups
# walk the pages until the address turns up.
LOOKUP_PAGE_SIZE = 200
LOOKUP_MAX_PAGES = 50  # 10,000 accounts at the default size


def _client() -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` pre-configured for the Supabase project.

    This is the single seam the test-suite overrides.
    """
    return httpx.AsyncClient(
        base_url=settings.SUPABASE_URL,
        headers={
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        },
        timeout=15.0,
    )


def _url(path: str) -> str:
    """Build an absolute Supabase URL so calls work even when the client has no base_url."""
    return f"{settings.SUPABASE_URL.rstrip('/')}{path}"


async def lookup_user_id_by_email(email: str) -> UUID | None:
    """Return the auth user id for ``email``, or ``None`` if no account exists.

    Walks GoTrue's paginated admin user list. It stops at the match, when the
    reported ``X-Total-Count`` of distinct users has been seen, or on an empty
    page (when the server sends no count). It does not assume a short page means
    the last page, because the server may cap the page size. Past
    ``LOOKUP_MAX_PAGES`` it raises rather than claim the account doesn't exist.
    The address is deliberately not put in the request URL (the server ignores
    it, and URLs end up in logs).
    """
    wanted = email.strip().lower()
    seen_ids: set[str] = set()
    async with _client() as c:
        for page in range(1, LOOKUP_MAX_PAGES + 1):
            resp = await c.get(
                _url(f"/auth/v1/admin/users?page={page}&per_page={LOOKUP_PAGE_SIZE}")
            )
            resp.raise_for_status()
            body = resp.json()
            users = body if isinstance(body, list) else body.get("users", [])

            for user in users:
                got = user.get("email")
                if isinstance(got, str) and got.strip().lower() == wanted:
                    return UUID(user["id"])
                seen_ids.add(str(user.get("id")))

            if not users:
                return None
            try:
                total = int(resp.headers.get("x-total-count", ""))
            except ValueError:
                total = None
            if total is not None and len(seen_ids) >= total:
                return None

    raise RuntimeError(
        f"User lookup gave up after {LOOKUP_MAX_PAGES} pages of {LOOKUP_PAGE_SIZE}; "
        "too many accounts to search this way"
    )


async def upload_object(path: str, data: bytes, content_type: str) -> None:
    """Upload ``data`` to the configured Storage bucket at ``path`` (no leading slash)."""
    bucket = settings.SUPABASE_STORAGE_BUCKET
    async with _client() as c:
        resp = await c.put(
            _url(f"/storage/v1/object/{bucket}/{path}"),
            content=data,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage upload failed: {resp.status_code} {resp.text}")


async def delete_object(path: str) -> None:
    """Delete the object at ``path`` from the configured Storage bucket."""
    bucket = settings.SUPABASE_STORAGE_BUCKET
    async with _client() as c:
        await c.delete(_url(f"/storage/v1/object/{bucket}/{path}"))


class StorageObjectNotFound(Exception):
    """Raised when Storage reports that the requested object does not exist."""


async def create_signed_url(path: str, expires_in: int) -> str:
    """Return an absolute, short-lived signed URL for the object at ``path``.

    Raises ``StorageObjectNotFound`` if the object is missing and ``RuntimeError``
    for any other failure. The URL, token and response body are never logged.
    """
    bucket = settings.SUPABASE_STORAGE_BUCKET
    async with _client() as c:
        resp = await c.post(
            _url(f"/storage/v1/object/sign/{bucket}/{quote(path, safe='/')}"),
            json={"expiresIn": expires_in},
        )

    if resp.status_code == 404:
        raise StorageObjectNotFound()
    if resp.status_code == 400 and "not found" in resp.text.lower():
        raise StorageObjectNotFound()
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage sign failed: {resp.status_code}")

    try:
        signed = resp.json().get("signedURL")
    except (ValueError, AttributeError):
        raise RuntimeError("Storage sign returned an invalid body") from None
    if not isinstance(signed, str):
        raise RuntimeError("Storage sign returned an invalid body")
    return f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1{signed}"
