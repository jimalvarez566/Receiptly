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

# GoTrue's admin user list is paginated and ignores the ``email`` query
# parameter, so lookups walk the pages until the address turns up.
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
    reported ``X-Total-Count`` has been seen, on an empty page (when the server
    sends no count), or at ``LOOKUP_MAX_PAGES``. It does not assume a short page
    means the last page, because the server may cap the page size.
    """
    wanted = email.strip().lower()
    # Build the query string directly (rather than via ``params=``) so the "@" in
    # the address stays literal in the request URL.
    query = quote(email.strip(), safe="@")
    seen = 0
    async with _client() as c:
        for page in range(1, LOOKUP_MAX_PAGES + 1):
            resp = await c.get(
                _url(
                    f"/auth/v1/admin/users?email={query}"
                    f"&page={page}&per_page={LOOKUP_PAGE_SIZE}"
                )
            )
            resp.raise_for_status()
            body = resp.json()
            users = body if isinstance(body, list) else body.get("users", [])

            for user in users:
                got = user.get("email")
                if isinstance(got, str) and got.strip().lower() == wanted:
                    return UUID(user["id"])

            seen += len(users)
            if not users:
                return None
            total = resp.headers.get("x-total-count")
            if total is not None and total.isdigit() and seen >= int(total):
                return None

    logger.warning(
        "User lookup hit the page cap (%d pages of %d) without finding the address",
        LOOKUP_MAX_PAGES,
        LOOKUP_PAGE_SIZE,
    )
    return None


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
