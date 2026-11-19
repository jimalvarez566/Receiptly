import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Invitation, Membership, Tenant
from app.services.invitations import hash_token, invitation_status, new_token
from tests.conftest import make_token

NOT_ADMIN = "Only admins can manage members"


async def _seed(db_session, admin_id, member_ids=(), name="Acme"):
    tenant = Tenant(name=name, created_by_user_id=uuid.UUID(admin_id))
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(admin_id), role="admin"))
    for m in member_ids:
        db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(m), role="member"))
    await db_session.commit()
    return tenant


def _h(user_id, tenant_id):
    return {"Authorization": f"Bearer {make_token(user_id)}", "X-Tenant-ID": str(tenant_id)}


def _inv(tenant_id, admin_id, token="t", **kw):
    return Invitation(
        tenant_id=tenant_id,
        role=kw.pop("role", "member"),
        token_hash=hash_token(token),
        created_by_user_id=uuid.UUID(admin_id),
        expires_at=kw.pop("expires_at", datetime.utcnow() + timedelta(days=7)),
        **kw,
    )


async def test_admin_creates_member_and_admin_invites(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    url = f"/api/v1/tenants/{t.id}/invitations"
    for role in ("member", "admin"):
        r = await client.post(url, json={"role": role}, headers=_h(user_a_id, t.id))
        assert r.status_code == 201
        body = r.json()
        assert body["role"] == role and body["token"]
        row = (await db_session.execute(
            select(Invitation).where(Invitation.id == body["id"]))).scalar_one()
        assert row.token_hash == hash_token(body["token"])
        assert row.token_hash != body["token"]
        assert row.created_by_user_id == uuid.UUID(user_a_id)
        delta = row.expires_at - datetime.utcnow()
        assert timedelta(days=7) - timedelta(minutes=1) < delta <= timedelta(days=7)


async def test_default_role_is_member(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.post(f"/api/v1/tenants/{t.id}/invitations", json={},
                          headers=_h(user_a_id, t.id))
    assert r.status_code == 201
    assert r.json()["role"] == "member"


async def test_member_gets_exact_403_on_all_three_endpoints(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id, member_ids=[user_b_id])
    url = f"/api/v1/tenants/{t.id}/invitations"
    h = _h(user_b_id, t.id)
    for r in (
        await client.post(url, json={}, headers=h),
        await client.get(url, headers=h),
        await client.delete(f"{url}/99999", headers=h),
    ):
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ADMIN)


async def test_list_never_contains_token_or_hash(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    url = f"/api/v1/tenants/{t.id}/invitations"
    created = (await client.post(url, json={}, headers=_h(user_a_id, t.id))).json()
    r = await client.get(url, headers=_h(user_a_id, t.id))
    assert r.status_code == 200
    assert created["token"] not in r.text
    assert hash_token(created["token"]) not in r.text
    assert "token" not in r.text
    assert set(r.json()[0]) == {
        "id", "role", "status", "created_at", "expires_at",
        "created_by_user_id", "accepted_by_user_id",
    }


async def test_list_is_newest_first_limited_to_50_and_own_business_only(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed(db_session, user_a_id)
    other = await _seed(db_session, user_b_id, name="Other")
    base = datetime.utcnow() - timedelta(hours=2)
    for i in range(55):
        db_session.add(_inv(t.id, user_a_id, token=f"a{i}", created_at=base + timedelta(seconds=i)))
    db_session.add(_inv(other.id, user_b_id, token="other"))
    await db_session.commit()
    r = await client.get(f"/api/v1/tenants/{t.id}/invitations", headers=_h(user_a_id, t.id))
    rows = r.json()
    assert len(rows) == 50
    created = [x["created_at"] for x in rows]
    assert created == sorted(created, reverse=True)
    # exactly the 50 newest: the 5 oldest (a0..a4) are absent
    ids = [x["id"] for x in rows]
    hashes_present = (await db_session.execute(
        select(Invitation.id).where(Invitation.token_hash.in_(
            [hash_token(f"a{i}") for i in range(5)])))).scalars().all()
    assert not set(hashes_present) & set(ids)
    assert all(x["created_by_user_id"] == user_a_id for x in rows)


async def test_status_values(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    now = datetime.utcnow()
    db_session.add_all([
        _inv(t.id, user_a_id, "p", created_at=now - timedelta(seconds=4)),
        _inv(t.id, user_a_id, "u", created_at=now - timedelta(seconds=3),
             accepted_at=now, accepted_by_user_id=uuid.UUID(user_b_id)),
        _inv(t.id, user_a_id, "r", created_at=now - timedelta(seconds=2), revoked_at=now),
        _inv(t.id, user_a_id, "e", created_at=now - timedelta(seconds=1),
             expires_at=now - timedelta(minutes=1)),
    ])
    await db_session.commit()
    r = await client.get(f"/api/v1/tenants/{t.id}/invitations", headers=_h(user_a_id, t.id))
    assert [x["status"] for x in r.json()] == ["expired", "revoked", "used", "pending"]
    used = [x for x in r.json() if x["status"] == "used"][0]
    assert used["accepted_by_user_id"] == user_b_id


async def test_revoke_pending_sets_revoked_at_and_is_idempotent(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    tid = t.id
    inv = _inv(tid, user_a_id)
    db_session.add(inv)
    await db_session.flush()
    url = f"/api/v1/tenants/{tid}/invitations/{inv.id}"
    await db_session.commit()
    assert (await client.delete(url, headers=_h(user_a_id, tid))).status_code == 204
    db_session.expire_all()
    first = (await db_session.execute(select(Invitation.revoked_at))).scalar_one()
    assert first is not None
    assert (await client.delete(url, headers=_h(user_a_id, tid))).status_code == 204
    db_session.expire_all()
    assert (await db_session.execute(select(Invitation.revoked_at))).scalar_one() == first


async def test_revoke_used_is_409_with_exact_message(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    inv = _inv(t.id, user_a_id, accepted_at=datetime.utcnow(),
               accepted_by_user_id=uuid.UUID(user_b_id))
    db_session.add(inv)
    await db_session.commit()
    r = await client.delete(f"/api/v1/tenants/{t.id}/invitations/{inv.id}",
                            headers=_h(user_a_id, t.id))
    assert (r.status_code, r.json()["detail"]) == (409, "That invitation was already used")
    db_session.expire_all()
    assert (await db_session.execute(select(Invitation.revoked_at))).scalar_one() is None


async def test_revoke_other_business_invitation_is_404_and_unchanged(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed(db_session, user_a_id)
    other = await _seed(db_session, user_b_id, name="Other")
    inv = _inv(other.id, user_b_id)
    db_session.add(inv)
    await db_session.commit()
    r = await client.delete(f"/api/v1/tenants/{t.id}/invitations/{inv.id}",
                            headers=_h(user_a_id, t.id))
    assert (r.status_code, r.json()["detail"]) == (404, "Invitation not found")
    db_session.expire_all()
    assert (await db_session.execute(select(Invitation.revoked_at))).scalar_one() is None


async def test_tenant_path_must_match_header(client, db_session, user_a_id, user_b_id):
    t = await _seed(db_session, user_a_id)
    other = await _seed(db_session, user_b_id, name="Other")
    h = _h(user_a_id, t.id)
    url = f"/api/v1/tenants/{other.id}/invitations"
    assert (await client.post(url, json={}, headers=h)).status_code == 403
    assert (await client.get(url, headers=h)).status_code == 403
    assert (await client.delete(f"{url}/1", headers=h)).status_code == 403


async def test_token_and_hash_not_logged(client, db_session, user_a_id, caplog):
    t = await _seed(db_session, user_a_id)
    url = f"/api/v1/tenants/{t.id}/invitations"
    h = _h(user_a_id, t.id)
    with caplog.at_level(logging.DEBUG):
        created = (await client.post(url, json={}, headers=h)).json()
        await client.get(url, headers=h)
        await client.delete(f"{url}/{created['id']}", headers=h)
    token = created["token"]
    for rec in caplog.records:
        text = rec.getMessage() + str(rec.args)
        assert token not in text and hash_token(token) not in text


def test_hash_token_is_stable_64_hex():
    h = hash_token("abc")
    assert h == hash_token("abc") and len(h) == 64
    int(h, 16)
    assert h != hash_token("abd")


def test_new_token_distinct_and_long():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43


def test_invitation_status_all_cases():
    now = datetime(2026, 1, 1, 12, 0, 0)
    future, past = now + timedelta(days=1), now - timedelta(days=1)

    def mk(**kw):
        return Invitation(role="member", token_hash="x", expires_at=kw.pop("expires_at", future), **kw)

    assert invitation_status(mk(), now) == "pending"
    assert invitation_status(mk(accepted_at=past), now) == "used"
    assert invitation_status(mk(revoked_at=past), now) == "revoked"
    assert invitation_status(mk(expires_at=past), now) == "expired"
    assert invitation_status(mk(expires_at=now), now) == "expired"
    assert invitation_status(mk(accepted_at=past, revoked_at=past), now) == "revoked"
    assert invitation_status(mk(accepted_at=past, expires_at=past), now) == "used"


async def test_list_ties_on_created_at_come_back_in_descending_id_order(
    client, db_session, user_a_id
):
    t = await _seed(db_session, user_a_id)
    same = datetime.utcnow() - timedelta(hours=1)
    for i in range(3):
        db_session.add(_inv(t.id, user_a_id, token=f"tie{i}", created_at=same))
    await db_session.commit()
    r = await client.get(f"/api/v1/tenants/{t.id}/invitations", headers=_h(user_a_id, t.id))
    ids = [x["id"] for x in r.json()]
    assert len(ids) == 3 and ids == sorted(ids, reverse=True)


async def test_invalid_role_is_422(client, db_session, user_a_id):
    t = await _seed(db_session, user_a_id)
    r = await client.post(f"/api/v1/tenants/{t.id}/invitations", json={"role": "owner"},
                          headers=_h(user_a_id, t.id))
    assert r.status_code == 422


def test_app_engine_hides_parameters():
    from app.database import engine
    assert engine.sync_engine.hide_parameters is True


async def test_engine_factory_keeps_parameter_values_out_of_logs(caplog):
    import os
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool
    from app.database import _make_engine

    eng = _make_engine(os.environ["DATABASE_URL"], echo=True, poolclass=NullPool)
    secret = "SECRET-PARAM-VALUE-12345"
    try:
        with caplog.at_level(logging.INFO, logger="sqlalchemy.engine"):
            async with eng.connect() as conn:
                await conn.execute(text("SELECT CAST(:v AS TEXT)"), {"v": secret})
    finally:
        await eng.dispose()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "SELECT" in logged
    assert secret not in logged
