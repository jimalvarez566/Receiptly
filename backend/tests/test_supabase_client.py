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


# --- lookup pagination -------------------------------------------------------

def _paged_transport(monkeypatch, pages, total=None):
    """Serve ``pages`` (list of lists of user dicts) for GoTrue admin list calls.

    Like the real service, replies carry an ``X-Total-Count`` header when
    ``total`` is given. Every request is recorded so tests can assert how many
    pages were fetched.
    """
    requests = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", "1"))
        users = pages[page - 1] if page - 1 < len(pages) else []
        headers = {"X-Total-Count": str(total)} if total is not None else {}
        return httpx.Response(
            200, json={"aud": "authenticated", "users": users}, headers=headers
        )

    monkeypatch.setattr(
        supabase_client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    return requests


def _users(n, start=0):
    return [
        {"id": f"00000000-0000-0000-0000-{i:012d}", "email": f"user{i}@x.com"}
        for i in range(start, start + n)
    ]


async def test_lookup_finds_user_on_a_later_page(monkeypatch):
    size = supabase_client.LOOKUP_PAGE_SIZE
    target = {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "email": "later@x.com"}
    requests = _paged_transport(monkeypatch, [_users(size), [target]], total=size + 1)

    uid = await supabase_client.lookup_user_id_by_email("later@x.com")

    assert str(uid) == target["id"]
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert all(r.url.params["per_page"] == str(size) for r in requests)


async def test_lookup_stops_once_the_total_count_is_reached(monkeypatch):
    requests = _paged_transport(monkeypatch, [_users(3)], total=3)

    assert await supabase_client.lookup_user_id_by_email("nobody@x.com") is None
    assert len(requests) == 1


async def test_lookup_without_a_total_header_stops_on_an_empty_page(monkeypatch):
    requests = _paged_transport(monkeypatch, [_users(3), []])

    assert await supabase_client.lookup_user_id_by_email("nobody@x.com") is None
    assert len(requests) == 2


async def test_lookup_survives_a_server_that_caps_the_page_size(monkeypatch):
    # Asked for LOOKUP_PAGE_SIZE per page but the server returns only 50 at a time.
    target = {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "email": "far@x.com"}
    requests = _paged_transport(
        monkeypatch, [_users(50), _users(50, start=50), [target]], total=101
    )

    uid = await supabase_client.lookup_user_id_by_email("far@x.com")

    assert str(uid) == target["id"]
    assert len(requests) == 3


async def test_lookup_returns_match_without_fetching_further_pages(monkeypatch):
    size = supabase_client.LOOKUP_PAGE_SIZE
    users = _users(size)
    requests = _paged_transport(monkeypatch, [users, _users(size, start=size)], total=2 * size)

    uid = await supabase_client.lookup_user_id_by_email("USER5@x.com")  # case-insensitive

    assert str(uid) == users[5]["id"]
    assert len(requests) == 1


async def test_lookup_gives_up_at_the_page_cap(monkeypatch, caplog):
    monkeypatch.setattr(supabase_client, "LOOKUP_MAX_PAGES", 3)
    size = supabase_client.LOOKUP_PAGE_SIZE
    pages = [_users(size, start=i * size) for i in range(10)]
    requests = _paged_transport(monkeypatch, pages, total=10 * size)

    with caplog.at_level("WARNING"):
        assert await supabase_client.lookup_user_id_by_email("missing@x.com") is None

    assert len(requests) == 3
    assert any("page cap" in r.getMessage() for r in caplog.records)


async def test_lookup_accepts_a_bare_list_body(monkeypatch):
    target = {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "email": "list@x.com"}

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[target])

    monkeypatch.setattr(
        supabase_client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    uid = await supabase_client.lookup_user_id_by_email("list@x.com")
    assert str(uid) == target["id"]
