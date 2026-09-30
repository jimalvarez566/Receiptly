import uuid

from app.models import Membership, Tenant
from tests.conftest import make_token


async def test_me_lists_memberships(client, db_session, user_a_id):
    uid = uuid.UUID(user_a_id)
    t1 = Tenant(name="AceCo", created_by_user_id=uid)
    t2 = Tenant(name="BeeCo", created_by_user_id=uid)
    db_session.add_all([t1, t2])
    await db_session.flush()
    db_session.add_all([
        Membership(tenant_id=t1.id, user_id=uid, role="owner"),
        Membership(tenant_id=t2.id, user_id=uid, role="member"),
    ])
    await db_session.commit()

    token = make_token(user_a_id, "a@b.com")
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "a@b.com"
    got = {(m["name"], m["role"]) for m in body["memberships"]}
    assert got == {("AceCo", "owner"), ("BeeCo", "member")}


async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
