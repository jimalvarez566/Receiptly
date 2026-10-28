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
