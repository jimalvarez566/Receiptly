import uuid

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    RequestContext,
    can_view_receipt,
    get_current_context,
    require_admin,
)
from app.models.receipt import Receipt


def _ctx(role: str) -> RequestContext:
    return RequestContext(
        user_id=uuid.uuid4(), email="a@example.com", tenant_id=1, role=role
    )


def _client(role: str) -> AsyncClient:
    probe = FastAPI()

    @probe.get("/thing")
    async def thing(ctx: RequestContext = Depends(require_admin("do the thing"))):
        return {"role": ctx.role}

    async def fake_ctx():
        return _ctx(role)

    probe.dependency_overrides[get_current_context] = fake_ctx
    return AsyncClient(transport=ASGITransport(app=probe), base_url="http://test")


def test_role_constants():
    assert ROLE_ADMIN == "admin"
    assert ROLE_MEMBER == "member"


async def test_require_admin_allows_admin():
    async with _client(ROLE_ADMIN) as c:
        resp = await c.get("/thing")
    assert resp.status_code == 200
    assert resp.json() == {"role": "admin"}


async def test_require_admin_rejects_member():
    async with _client(ROLE_MEMBER) as c:
        resp = await c.get("/thing")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only admins can do the thing"


def test_admin_can_view_any_receipt():
    r = Receipt(submitted_by_user_id=uuid.uuid4())
    assert can_view_receipt(_ctx(ROLE_ADMIN), r) is True


def test_member_can_view_own_receipt():
    ctx = _ctx(ROLE_MEMBER)
    assert can_view_receipt(ctx, Receipt(submitted_by_user_id=ctx.user_id)) is True


def test_member_cannot_view_others_receipt():
    r = Receipt(submitted_by_user_id=uuid.uuid4())
    assert can_view_receipt(_ctx(ROLE_MEMBER), r) is False
