import json

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


# --- create_signed_url -------------------------------------------------------


@pytest.fixture
def sign_transport(monkeypatch):
    """Separate from ``mock_transport`` (which swallows POSTs to /storage/v1/object/)."""
    state = {"response": httpx.Response(200, json={"signedURL": "/object/sign/receipts/1/abc.png?token=T"})}

    def _handler(request: httpx.Request) -> httpx.Response:
        state["last"] = request
        return state["response"]

    monkeypatch.setattr(supabase_client.settings, "SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setattr(supabase_client.settings, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    real_client = supabase_client._client  # built after settings patched: real headers, mock transport

    def _client():
        c = real_client()
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_handler),
            headers=c.headers,
        )

    monkeypatch.setattr(supabase_client, "_client", _client)
    return state


async def test_create_signed_url_posts_expected_request_and_returns_absolute_url(sign_transport):
    url = await supabase_client.create_signed_url("1/abc.png", 300)
    req = sign_transport["last"]
    assert req.method == "POST"
    assert req.url.path == "/storage/v1/object/sign/receipts/1/abc.png"
    assert json.loads(req.content) == {"expiresIn": 300}
    assert req.headers["Authorization"] == "Bearer service-key"
    assert url == "https://proj.supabase.co/storage/v1/object/sign/receipts/1/abc.png?token=T"


async def test_create_signed_url_encodes_special_characters_once(sign_transport):
    await supabase_client.create_signed_url("1/my receipt #1.png", 300)
    raw = sign_transport["last"].url.raw_path
    assert b"my%20receipt%20%231.png" in raw
    assert b"%2520" not in raw


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"message": "whatever"}),
        httpx.Response(
            400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"}
        ),
    ],
)
async def test_create_signed_url_missing_object_raises_not_found(sign_transport, response):
    sign_transport["response"] = response
    with pytest.raises(supabase_client.StorageObjectNotFound):
        await supabase_client.create_signed_url("1/abc.png", 300)


async def test_create_signed_url_other_failure_raises_runtime_error(sign_transport):
    sign_transport["response"] = httpx.Response(500, text="boom")
    with pytest.raises(RuntimeError) as exc:
        await supabase_client.create_signed_url("1/abc.png", 300)
    assert not isinstance(exc.value, supabase_client.StorageObjectNotFound)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"other": "x"}),
        httpx.Response(200, json={"signedURL": 123}),
    ],
)
async def test_create_signed_url_malformed_body_raises_runtime_error(sign_transport, response):
    sign_transport["response"] = response
    with pytest.raises(RuntimeError):
        await supabase_client.create_signed_url("1/abc.png", 300)
