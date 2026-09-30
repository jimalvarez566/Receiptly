import httpx
import pytest

from app import supabase_client


@pytest.fixture
def mock_transport(monkeypatch):
    calls = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["last"] = request
        if "/auth/v1/admin/users" in request.url.path:
            if "known@x.com" in str(request.url):
                return httpx.Response(
                    200,
                    json={"users": [{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "email": "known@x.com"}]},
                )
            return httpx.Response(200, json={"users": []})
        if request.method in ("PUT", "POST") and "/storage/v1/object/" in request.url.path:
            return httpx.Response(200, json={"Key": "receipts/1/x.png"})
        if request.method == "DELETE" and "/storage/v1/object/" in request.url.path:
            return httpx.Response(200, json={})
        return httpx.Response(404)

    monkeypatch.setattr(
        supabase_client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    return calls


async def test_lookup_known_email_returns_uuid(mock_transport):
    uid = await supabase_client.lookup_user_id_by_email("known@x.com")
    assert str(uid) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


async def test_lookup_unknown_email_returns_none(mock_transport):
    assert await supabase_client.lookup_user_id_by_email("nobody@x.com") is None


async def test_upload_object_posts_bytes(mock_transport):
    await supabase_client.upload_object("1/x.png", b"data", "image/png")
    req = mock_transport["last"]
    assert req.method == "PUT"
    assert "/storage/v1/object/receipts/1/x.png" in req.url.path


async def test_delete_object(mock_transport):
    await supabase_client.delete_object("1/x.png")
    req = mock_transport["last"]
    assert req.method == "DELETE"
