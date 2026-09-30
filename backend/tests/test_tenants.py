import uuid

import pytest

from app import supabase_client
from app.models import Membership, Tenant
from tests.conftest import make_token


async def _seed_tenant(db_session, owner_id: uuid.UUID, name="Acme"):
    tenant = Tenant(name=name, created_by_user_id=owner_id)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=owner_id, role="owner"))
    await db_session.commit()
    return tenant


async def test_create_tenant_makes_owner_membership(client, db_session, user_a_id):
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/tenants", json={"name": "Acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(Membership).where(Membership.tenant_id == tid)
    )).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].user_id) == user_a_id
    assert rows[0].role == "owner"


async def test_list_tenants_only_returns_own(client, db_session, user_a_id, user_b_id):
    await _seed_tenant(db_session, uuid.UUID(user_a_id), "AceCo")
    await _seed_tenant(db_session, uuid.UUID(user_b_id), "BeeCo")

    token = make_token(user_a_id)
    resp = await client.get(
        "/api/v1/tenants", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert names == {"AceCo"}


async def test_add_member_with_known_email(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    new_id = uuid.uuid4()

    async def fake_lookup(email):
        return new_id

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)

    token = make_token(user_a_id)
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/members",
        json={"email": "new@x.com"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 201

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(Membership).where(
            Membership.tenant_id == tenant.id
        )
    )).scalars().all()
    assert {str(r.user_id) for r in rows} == {user_a_id, str(new_id)}


async def test_add_member_unknown_email_is_404(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))

    async def fake_lookup(email):
        return None

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)

    token = make_token(user_a_id)
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/members",
        json={"email": "ghost@x.com"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 404


async def test_add_member_idempotent_returns_200(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    existing = uuid.uuid4()
    db_session.add(Membership(tenant_id=tenant.id, user_id=existing, role="member"))
    await db_session.commit()

    async def fake_lookup(email):
        return existing

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)
    token = make_token(user_a_id)
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/members",
        json={"email": "existing@x.com"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 200


async def test_remove_member_success(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    victim = uuid.uuid4()
    db_session.add(Membership(tenant_id=tenant.id, user_id=victim, role="member"))
    await db_session.commit()

    token = make_token(user_a_id)
    resp = await client.request(
        "DELETE",
        f"/api/v1/tenants/{tenant.id}/members/{victim}",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 204

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(Membership).where(Membership.tenant_id == tenant.id)
    )).scalars().all()
    assert {str(r.user_id) for r in rows} == {user_a_id}


async def test_remove_member_bad_uuid_is_404(client, db_session, user_a_id):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    token = make_token(user_a_id)
    resp = await client.request(
        "DELETE",
        f"/api/v1/tenants/{tenant.id}/members/not-a-uuid",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 404


async def test_non_member_cannot_list_members(client, db_session, user_a_id, user_b_id):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    token = make_token(user_b_id)
    resp = await client.get(
        f"/api/v1/tenants/{tenant.id}/members",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 403


from app.models.policy_rule import PolicyRule
from app.services.policy_defaults import DEFAULT_RULES


async def test_create_tenant_seeds_default_policies(client, db_session, user_a_id):
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/tenants", json={"name": "Acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(PolicyRule).where(PolicyRule.tenant_id == tid)
    )).scalars().all()
    assert len(rows) == len(DEFAULT_RULES)
    assert all(r.tenant_id == tid for r in rows)
    assert all(r.is_active is True for r in rows)
