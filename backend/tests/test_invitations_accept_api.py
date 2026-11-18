import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.invitations import accept_invitation, revoke_invitation
from app.api.v1.receipts import limiter
from app.auth import CurrentUser, RequestContext
from app.models import Invitation, Membership, Tenant
from app.services.invitations import hash_token, new_token
from tests.conftest import TestSessionLocal, make_token

INVALID = "This invitation link isn't valid"
REVOKED = "This invitation was revoked"
USED = "This invitation has already been used"
EXPIRED = "This invitation has expired"


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


async def _seed(db_session, admin_id, name="Acme"):
    tenant = Tenant(name=name, created_by_user_id=uuid.UUID(admin_id))
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(admin_id), role="admin"))
    await db_session.commit()
    return tenant


async def _invite(db_session, tenant_id, admin_id, role="member", **kw):
    token = new_token()
    inv = Invitation(
        tenant_id=tenant_id,
        role=role,
        token_hash=hash_token(token),
        created_by_user_id=uuid.UUID(admin_id),
        expires_at=kw.pop("expires_at", datetime.utcnow() + timedelta(days=7)),
        **kw,
    )
    db_session.add(inv)
    await db_session.commit()
    return token, inv


def _auth(user_id):
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _user(user_id):
    return CurrentUser(user_id=uuid.UUID(user_id), email=None)


async def _members(db_session, tenant_id):
    return (await db_session.execute(
        select(func.count()).select_from(Membership).where(Membership.tenant_id == tenant_id)
    )).scalar_one()


async def _reload(db_session, inv_id):
    return (await db_session.execute(
        select(Invitation).where(Invitation.id == inv_id)
        .execution_options(populate_existing=True))).scalar_one()


# ---------------------------------------------------------------- preview

async def test_preview_valid_invite_returns_name_role_expiry_without_login(
        client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id, name="Cafe Rio")
    token, inv = await _invite(db_session, t.id, user_a_id, role="admin")
    r = await client.get(f"/api/v1/invitations/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_name"] == "Cafe Rio"
    assert body["role"] == "admin"
    assert datetime.fromisoformat(body["expires_at"]) == inv.expires_at


@pytest.mark.parametrize("kind,code,msg", [
    ("unknown", 404, INVALID),
    ("revoked", 410, REVOKED),
    ("used", 410, USED),
    ("expired", 410, EXPIRED),
])
async def test_preview_error_messages(client, db_session, user_a_id, kind, code, msg):
    t = await _seed(db_session, user_a_id)
    kw = {}
    if kind == "revoked":
        kw["revoked_at"] = datetime.utcnow()
    if kind == "used":
        kw.update(accepted_at=datetime.utcnow(), accepted_by_user_id=uuid.uuid4())
    if kind == "expired":
        kw["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
    token, _ = await _invite(db_session, t.id, user_a_id, **kw)
    if kind == "unknown":
        token = new_token()
    r = await client.get(f"/api/v1/invitations/{token}")
    assert (r.status_code, r.json()["detail"]) == (code, msg)


async def test_preview_does_not_leak_token_or_ids(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    token, inv = await _invite(db_session, t.id, user_a_id)
    r = await client.get(f"/api/v1/invitations/{token}")
    assert set(r.json()) == {"tenant_name", "role", "expires_at"}
    assert token not in r.text and inv.token_hash not in r.text
    assert user_a_id not in r.text


async def test_preview_is_rate_limited_to_429(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    token, _ = await _invite(db_session, t.id, user_a_id)
    codes = [(await client.get(f"/api/v1/invitations/{token}")).status_code
             for _ in range(61)]
    assert codes[:60] == [200] * 60
    assert codes[60] == 429


# ---------------------------------------------------------------- accept

@pytest.mark.parametrize("role", ["member", "admin"])
async def test_accept_creates_membership_with_the_invite_role_and_marks_used(
        client, db_session, user_a_id, user_b_id, role):
    t = await _seed(db_session, user_a_id, name="Cafe Rio")
    token, inv = await _invite(db_session, t.id, user_a_id, role=role)
    r = await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_b_id))
    assert r.status_code == 200
    assert r.json() == {"tenant_id": t.id, "tenant_name": "Cafe Rio",
                        "role": role, "already_member": False}
    m = (await db_session.execute(select(Membership).where(
        Membership.tenant_id == t.id, Membership.user_id == uuid.UUID(user_b_id)))).scalar_one()
    assert m.role == role
    row = await _reload(db_session, inv.id)
    assert row.accepted_at is not None
    assert row.accepted_by_user_id == uuid.UUID(user_b_id)
    assert row.revoked_at is None


async def test_accept_requires_login_401(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    token, _ = await _invite(db_session, t.id, user_a_id)
    url = f"/api/v1/invitations/{token}/accept"
    assert (await client.post(url)).status_code == 401
    bad = {"Authorization": f"Bearer {make_token(user_a_id, expired=True)}"}
    assert (await client.post(url, headers=bad)).status_code == 401
    assert await _members(db_session, t.id) == 1


async def test_accept_twice_by_another_user_is_410_used(
        client, db_session, user_a_id, user_b_id, user_c_id):
    t = await _seed(db_session, user_a_id)
    token, _ = await _invite(db_session, t.id, user_a_id)
    url = f"/api/v1/invitations/{token}/accept"
    assert (await client.post(url, headers=_auth(user_b_id))).status_code == 200
    r = await client.post(url, headers=_auth(user_c_id))
    assert (r.status_code, r.json()["detail"]) == (410, USED)
    assert await _members(db_session, t.id) == 2


async def test_accept_expired_and_revoked_are_410_with_exact_messages(
        client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    for kw, msg in (
        ({"expires_at": datetime.utcnow() - timedelta(minutes=1)}, EXPIRED),
        ({"revoked_at": datetime.utcnow()}, REVOKED),
    ):
        token, _ = await _invite(db_session, t.id, user_a_id, **kw)
        r = await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_b_id))
        assert (r.status_code, r.json()["detail"]) == (410, msg)
    assert await _members(db_session, t.id) == 1


async def test_accept_unknown_token_404(client, db_session, user_b_id):
    r = await client.post(f"/api/v1/invitations/{new_token()}/accept", headers=_auth(user_b_id))
    assert (r.status_code, r.json()["detail"]) == (404, INVALID)


async def test_accept_by_existing_member_is_200_already_member_and_leaves_invite_pending(
        client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id, name="Cafe Rio")
    token, inv = await _invite(db_session, t.id, user_a_id)
    r = await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_a_id))
    assert r.status_code == 200
    assert r.json() == {"tenant_id": t.id, "tenant_name": "Cafe Rio",
                        "role": "admin", "already_member": True}
    row = await _reload(db_session, inv.id)
    assert row.accepted_at is None and row.accepted_by_user_id is None
    assert await _members(db_session, t.id) == 1


@pytest.mark.parametrize("kind", ["used", "expired", "revoked"])
async def test_already_member_wins_even_when_invite_is_used_expired_or_revoked(
        client, db_session, user_a_id, kind):
    t = await _seed(db_session, user_a_id)
    kw = {}
    if kind == "used":
        kw.update(accepted_at=datetime.utcnow(), accepted_by_user_id=uuid.uuid4())
    if kind == "expired":
        kw["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
    if kind == "revoked":
        kw["revoked_at"] = datetime.utcnow()
    token, _ = await _invite(db_session, t.id, user_a_id, **kw)
    r = await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_a_id))
    assert r.status_code == 200 and r.json()["already_member"] is True


async def test_existing_role_is_not_changed_by_accept(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    token, _ = await _invite(db_session, t.id, user_a_id, role="member")
    r = await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_a_id))
    assert r.json()["already_member"] is True and r.json()["role"] == "admin"
    role = (await db_session.execute(select(Membership.role).where(
        Membership.tenant_id == t.id, Membership.user_id == uuid.UUID(user_a_id)))).scalar_one()
    assert role == "admin"


# ---------------------------------------------------------------- concurrency

async def _blocked(task):
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
    assert not task.done()


async def test_two_people_accepting_concurrently_make_one_membership(
        db_session, user_a_id, user_b_id, user_c_id):
    t = await _seed(db_session, user_a_id)
    token, inv = await _invite(db_session, t.id, user_a_id)
    s1, s2 = TestSessionLocal(), TestSessionLocal()
    try:
        out = await accept_invitation(token, _user(user_b_id), s1)  # lock held
        assert out.already_member is False
        task = asyncio.create_task(accept_invitation(token, _user(user_c_id), s2))
        await _blocked(task)
        await s1.commit()
        with pytest.raises(HTTPException) as e:
            await task
        assert (e.value.status_code, e.value.detail) == (410, USED)
    finally:
        await s1.close()
        await s2.close()
    assert await _members(db_session, t.id) == 2
    row = await _reload(db_session, inv.id)
    assert row.accepted_by_user_id == uuid.UUID(user_b_id)


async def test_same_user_two_invites_concurrently_is_not_a_500(
        db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    tok1, inv1 = await _invite(db_session, t.id, user_a_id, role="member")
    tok2, inv2 = await _invite(db_session, t.id, user_a_id, role="admin")
    s1, s2 = TestSessionLocal(), TestSessionLocal()
    try:
        first = await accept_invitation(tok1, _user(user_b_id), s1)
        assert first.already_member is False
        # s2 blocks on the unique (tenant, user) index until s1 commits.
        task = asyncio.create_task(accept_invitation(tok2, _user(user_b_id), s2))
        await _blocked(task)
        await s1.commit()
        second = await task
        assert second.already_member is True
        assert second.role == "member"  # the role actually held, not the second invite's
        await s2.commit()  # session still usable after the savepoint rollback
    finally:
        await s1.close()
        await s2.close()
    assert await _members(db_session, t.id) == 2
    role = (await db_session.execute(select(Membership.role).where(
        Membership.tenant_id == t.id, Membership.user_id == uuid.UUID(user_b_id)))).scalar_one()
    assert role == "member"
    assert (await _reload(db_session, inv1.id)).accepted_at is not None
    assert (await _reload(db_session, inv2.id)).accepted_at is None


def _admin_ctx(user_id, tenant_id):
    return RequestContext(user_id=uuid.UUID(user_id), email=None,
                          tenant_id=tenant_id, role="admin")


async def test_revoke_waits_for_in_flight_accept_and_cannot_revoke_used_invite(
        db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    token, inv = await _invite(db_session, t.id, user_a_id)
    s1, s2 = TestSessionLocal(), TestSessionLocal()
    try:
        await accept_invitation(token, _user(user_b_id), s1)  # row locked, uncommitted
        task = asyncio.create_task(
            revoke_invitation(t.id, inv.id, _admin_ctx(user_a_id, t.id), s2))
        await _blocked(task)
        await s1.commit()
        with pytest.raises(HTTPException) as e:
            await task
        assert e.value.status_code == 409
    finally:
        await s1.close()
        await s2.close()
    row = await _reload(db_session, inv.id)
    assert row.accepted_at is not None and row.revoked_at is None


async def test_accept_waits_for_in_flight_revoke_and_gets_410_revoked(
        db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    token, inv = await _invite(db_session, t.id, user_a_id)
    s1, s2 = TestSessionLocal(), TestSessionLocal()
    try:
        await revoke_invitation(t.id, inv.id, _admin_ctx(user_a_id, t.id), s1)
        task = asyncio.create_task(accept_invitation(token, _user(user_b_id), s2))
        await _blocked(task)
        await s1.commit()
        with pytest.raises(HTTPException) as e:
            await task
        assert (e.value.status_code, e.value.detail) == (410, REVOKED)
    finally:
        await s1.close()
        await s2.close()
    row = await _reload(db_session, inv.id)
    assert row.revoked_at is not None and row.accepted_at is None
    assert await _members(db_session, t.id) == 1


# ---------------------------------------------------------------- logging

async def test_token_not_logged_on_preview_or_accept(
        client, db_session, user_a_id, user_b_id, caplog):
    t = await _seed(db_session, user_a_id)
    token, inv = await _invite(db_session, t.id, user_a_id)
    caplog.set_level(logging.DEBUG)
    await client.get(f"/api/v1/invitations/{token}")
    await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_b_id))
    await client.post(f"/api/v1/invitations/{token}/accept", headers=_auth(user_b_id))
    await client.get(f"/api/v1/invitations/{new_token()}")
    # httpx's own client-side request log (the test client) is not app logging.
    app_logs = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert token not in app_logs and inv.token_hash not in app_logs
