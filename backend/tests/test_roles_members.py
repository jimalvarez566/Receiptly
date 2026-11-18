import uuid

from sqlalchemy import select

from app import supabase_client
from app.models import Membership, Tenant
from tests.conftest import make_token

NOT_ADMIN = "Only admins can manage members"
LAST_ADMIN = "A business needs at least one admin"


async def _seed(db_session, admin_id: str, member_ids=(), extra_admin_ids=()):
    tenant = Tenant(name="Acme", created_by_user_id=uuid.UUID(admin_id))
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(admin_id), role="admin"))
    for m in extra_admin_ids:
        db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(m), role="admin"))
    for m in member_ids:
        db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(m), role="member"))
    await db_session.commit()
    return tenant


def _h(user_id, tenant_id):
    return {"Authorization": f"Bearer {make_token(user_id)}", "X-Tenant-ID": str(tenant_id)}


async def _role(db_session, tenant_id, user_id):
    db_session.expire_all()
    return (await db_session.execute(
        select(Membership.role).where(
            Membership.tenant_id == tenant_id, Membership.user_id == uuid.UUID(user_id))
    )).scalar_one_or_none()


async def test_member_cannot_add_remove_or_patch(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    base = f"/api/v1/tenants/{t.id}/members"
    h = _h(user_b_id, t.id)
    r = await client.post(base, json={"email": "x@x.com"}, headers=h)
    assert (r.status_code, r.json()["detail"]) == (403, NOT_ADMIN)
    r = await client.delete(f"{base}/{user_a_id}", headers=h)
    assert (r.status_code, r.json()["detail"]) == (403, NOT_ADMIN)
    r = await client.patch(f"{base}/{user_a_id}", json={"role": "member"}, headers=h)
    assert (r.status_code, r.json()["detail"]) == (403, NOT_ADMIN)


async def test_member_can_list_members(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    r = await client.get(f"/api/v1/tenants/{t.id}/members", headers=_h(user_b_id, t.id))
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_admin_adds_member_as_member(client, db_session, user_a_id, monkeypatch):
    t = await _seed(db_session, user_a_id)
    new_id = uuid.uuid4()

    async def fake_lookup(email):
        return new_id

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)
    r = await client.post(f"/api/v1/tenants/{t.id}/members", json={"email": "n@x.com"},
                          headers=_h(user_a_id, t.id))
    assert r.status_code == 201
    assert r.json()["role"] == "member"


async def test_admin_promotes_member(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    r = await client.patch(f"/api/v1/tenants/{t.id}/members/{user_b_id}",
                           json={"role": "admin"}, headers=_h(user_a_id, t.id))
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert await _role(db_session, t.id, user_b_id) == "admin"


async def test_admin_removes_member(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    r = await client.delete(f"/api/v1/tenants/{t.id}/members/{user_b_id}",
                            headers=_h(user_a_id, t.id))
    assert r.status_code == 204
    assert await _role(db_session, t.id, user_b_id) is None


async def test_sole_admin_cannot_remove_self(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.delete(f"/api/v1/tenants/{t.id}/members/{user_a_id}",
                            headers=_h(user_a_id, t.id))
    assert (r.status_code, r.json()["detail"]) == (400, LAST_ADMIN)
    assert await _role(db_session, t.id, user_a_id) == "admin"


async def test_sole_admin_cannot_demote_self(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.patch(f"/api/v1/tenants/{t.id}/members/{user_a_id}",
                           json={"role": "member"}, headers=_h(user_a_id, t.id))
    assert (r.status_code, r.json()["detail"]) == (400, LAST_ADMIN)
    assert await _role(db_session, t.id, user_a_id) == "admin"


async def test_admin_can_demote_self_when_another_admin(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, extra_admin_ids=[user_b_id])
    r = await client.patch(f"/api/v1/tenants/{t.id}/members/{user_a_id}",
                           json={"role": "member"}, headers=_h(user_a_id, t.id))
    assert r.status_code == 200
    assert r.json()["role"] == "member"


async def test_admin_can_remove_self_when_another_admin(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, extra_admin_ids=[user_b_id])
    r = await client.delete(f"/api/v1/tenants/{t.id}/members/{user_a_id}",
                            headers=_h(user_a_id, t.id))
    assert r.status_code == 204


async def test_patch_admin_to_admin_is_noop_ok(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.patch(f"/api/v1/tenants/{t.id}/members/{user_a_id}",
                           json={"role": "admin"}, headers=_h(user_a_id, t.id))
    assert r.status_code == 200


async def test_header_path_mismatch_still_403(client, db_session, user_a_id, user_b_id):
    t1 = await _seed(db_session, user_a_id)
    t2 = Tenant(name="Other", created_by_user_id=uuid.UUID(user_b_id))
    db_session.add(t2)
    await db_session.flush()
    db_session.add(Membership(tenant_id=t2.id, user_id=uuid.UUID(user_b_id), role="admin"))
    await db_session.commit()
    r = await client.delete(f"/api/v1/tenants/{t2.id}/members/{user_b_id}",
                            headers=_h(user_a_id, t1.id))
    assert r.status_code == 403
    assert await _role(db_session, t2.id, user_b_id) == "admin"


async def test_member_gets_admin_message_even_on_mismatch(client, db_session, user_a_id, user_b_id):
    t1 = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    r = await client.delete(f"/api/v1/tenants/{t1.id + 999}/members/{user_a_id}",
                            headers=_h(user_b_id, t1.id))
    assert (r.status_code, r.json()["detail"]) == (403, NOT_ADMIN)


async def test_patch_unknown_user_404(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.patch(f"/api/v1/tenants/{t.id}/members/{uuid.uuid4()}",
                           json={"role": "admin"}, headers=_h(user_a_id, t.id))
    assert (r.status_code, r.json()["detail"]) == (404, "Membership not found")


# --- stale-caller and concurrency tests (call endpoint logic directly) ---
import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1.tenants import remove_member, update_member_role
from app.auth import RequestContext
from app.schemas.tenant import MemberRoleUpdate
from tests.conftest import TestSessionLocal


def _ctx(user_id, tenant_id, role="admin"):
    return RequestContext(
        user_id=uuid.UUID(user_id), email=None, tenant_id=tenant_id, role=role
    )


async def test_stale_admin_caller_gets_403_after_lock(db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, extra_admin_ids=[user_b_id])
    # B's context says admin, but B was demoted in the DB after auth resolved.
    stale = _ctx(user_b_id, t.id)
    async with TestSessionLocal() as s:
        await s.execute(
            Membership.__table__.update()
            .where(Membership.user_id == uuid.UUID(user_b_id))
            .values(role="member")
        )
        await s.commit()
    with pytest.raises(HTTPException) as e:
        await update_member_role(t.id, user_b_id, MemberRoleUpdate(role="admin"), stale, db_session)
    assert (e.value.status_code, e.value.detail) == (403, NOT_ADMIN)
    with pytest.raises(HTTPException) as e:
        await remove_member(t.id, user_a_id, stale, db_session)
    assert (e.value.status_code, e.value.detail) == (403, NOT_ADMIN)


async def test_concurrent_demotions_serialise_on_admin_lock(db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, extra_admin_ids=[user_b_id])
    s1 = TestSessionLocal()
    s2 = TestSessionLocal()
    try:
        # s1: A demotes self, lock held (uncommitted).
        await update_member_role(
            t.id, user_a_id, MemberRoleUpdate(role="member"), _ctx(user_a_id, t.id), s1
        )
        # s2: B demotes self concurrently -> must block on the admin-row lock.
        task = asyncio.create_task(
            update_member_role(
                t.id, user_b_id, MemberRoleUpdate(role="member"), _ctx(user_b_id, t.id), s2
            )
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        assert not task.done()
        await s1.commit()
        with pytest.raises(HTTPException) as e:
            await task
        assert (e.value.status_code, e.value.detail) == (400, LAST_ADMIN)
    finally:
        await s1.close()
        await s2.close()
    assert await _role(db_session, t.id, user_b_id) == "admin"


async def test_concurrent_demotion_of_caller_yields_403(db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, extra_admin_ids=[user_b_id])
    s1 = TestSessionLocal()
    s2 = TestSessionLocal()
    try:
        # s1: A demotes B (uncommitted). s2: B (still admin per auth) removes A.
        await update_member_role(
            t.id, user_b_id, MemberRoleUpdate(role="member"), _ctx(user_a_id, t.id), s1
        )
        task = asyncio.create_task(remove_member(t.id, user_a_id, _ctx(user_b_id, t.id), s2))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        await s1.commit()
        with pytest.raises(HTTPException) as e:
            await task
        assert (e.value.status_code, e.value.detail) == (403, NOT_ADMIN)
    finally:
        await s1.close()
        await s2.close()
    assert await _role(db_session, t.id, user_a_id) == "admin"
