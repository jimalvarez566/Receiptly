"""Thin async wrappers over the Supabase REST API (GoTrue admin + Storage).

Every network call goes through the module-level ``_client()`` factory so tests
can monkeypatch it with a ``MockTransport``-backed ``httpx.AsyncClient``.
"""

from urllib.parse import quote
from uuid import UUID

import httpx

from app.config import settings


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
    """Return the auth user id for ``email``, or ``None`` if no account exists."""
    # Build the query string directly (rather than via ``params=``) so the "@" in
    # the address stays literal in the request URL, matching GoTrue's filter.
    query = quote(email.strip(), safe="@")
    async with _client() as c:
        resp = await c.get(_url(f"/auth/v1/admin/users?email={query}"))
    resp.raise_for_status()
    body = resp.json()

    if isinstance(body, list):
        users = body
    else:
        users = body.get("users", [])

    for user in users:
        got = user.get("email")
        if isinstance(got, str) and got.strip().lower() == email.strip().lower():
            return UUID(user["id"])
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
