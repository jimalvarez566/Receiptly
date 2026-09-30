import uuid

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import CurrentUser, get_current_user
from tests.conftest import make_token


@pytest.fixture
def probe_app():
    probe = FastAPI()

    @probe.get("/whoami")
    async def whoami(user: CurrentUser = Depends(get_current_user)):
        return {"user_id": str(user.user_id), "email": user.email}

    return probe


@pytest.fixture
async def probe_client(probe_app):
    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_valid_token_returns_user(probe_client):
    uid = str(uuid.uuid4())
    token = make_token(uid, "alice@example.com")
    resp = await probe_client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": uid, "email": "alice@example.com"}


async def test_missing_token_is_401(probe_client):
    resp = await probe_client.get("/whoami")
    assert resp.status_code == 401


async def test_malformed_token_is_401(probe_client):
    resp = await probe_client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_expired_token_is_401(probe_client):
    token = make_token(str(uuid.uuid4()), expired=True)
    resp = await probe_client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


import uuid as _uuid

from app.auth import RequestContext, get_current_context
from app.models import Membership, Tenant


@pytest.fixture
def ctx_app():
    from fastapi import FastAPI as _FastAPI

    probe = _FastAPI()

    @probe.get("/ctx")
    async def ctx(context: RequestContext = Depends(get_current_context)):
        return {
            "user_id": str(context.user_id),
            "tenant_id": context.tenant_id,
            "role": context.role,
        }

    return probe


@pytest.fixture
async def ctx_client(ctx_app, db_session):
    from app.database import get_db

    async def _override():
        yield db_session

    ctx_app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=ctx_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    ctx_app.dependency_overrides.clear()


async def test_context_resolves_membership(ctx_client, db_session):
    uid = _uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uid, role="owner"))
    await db_session.commit()

    token = make_token(str(uid))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(uid), "tenant_id": tenant.id, "role": "owner"}


async def test_context_missing_header_is_400(ctx_client):
    token = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get("/ctx", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


async def test_context_non_member_is_403(ctx_client, db_session):
    owner = _uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=owner)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=owner, role="owner"))
    await db_session.commit()

    outsider = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {outsider}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 403


async def test_context_bad_header_value_is_400(ctx_client):
    token = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "not-an-int"},
    )
    assert resp.status_code == 400
