import logging
import uuid

import pytest

from app import supabase_client
from app.api.v1 import receipts as receipts_module
from app.models import Membership, Receipt, Tenant
from tests.conftest import make_token

SIGNED = "https://signed.example/x?token=SECRET"


@pytest.fixture
def signer(monkeypatch):
    calls = []

    async def _sign(path, expires_in):
        calls.append((path, expires_in))
        return SIGNED

    monkeypatch.setattr(receipts_module.supabase_client, "create_signed_url", _sign)
    return calls


def _h(user_id, tenant_id):
    return {"Authorization": f"Bearer {make_token(user_id)}", "X-Tenant-ID": str(tenant_id)}


async def _seed_team(db_session, admin_id, member_id, name="AceCo"):
    tenant = Tenant(name=name, created_by_user_id=uuid.UUID(admin_id))
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(admin_id), role="admin"))
    db_session.add(Membership(tenant_id=tenant.id, user_id=uuid.UUID(member_id), role="member"))
    await db_session.commit()
    return tenant


async def _receipt(db_session, tenant, user_id, path="1/a.png"):
    r = Receipt(
        image_path=path, tenant_id=tenant.id,
        submitted_by_user_id=uuid.UUID(user_id), status="analyzed",
    )
    db_session.add(r)
    await db_session.commit()
    return r


def _url(rid):
    return f"/api/v1/receipts/{rid}/image-url"


async def test_admin_gets_url_for_any_receipt_in_business(
    client, db_session, user_a_id, user_b_id, signer
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.get(_url(r.id), headers=_h(user_a_id, t.id))
    assert resp.status_code == 200
    assert resp.json() == {"url": SIGNED, "expires_in": 300, "kind": "image"}
    assert resp.headers["cache-control"] == "no-store"
    assert signer == [("1/a.png", 300)]


async def test_member_gets_url_for_own_receipt(client, db_session, user_a_id, user_b_id, signer):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.get(_url(r.id), headers=_h(user_b_id, t.id))
    assert resp.status_code == 200
    assert len(signer) == 1


async def test_pdf_receipt_has_kind_pdf(client, db_session, user_a_id, user_b_id, signer):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id, path="1/a.PDF")
    resp = await client.get(_url(r.id), headers=_h(user_b_id, t.id))
    assert resp.status_code == 200
    assert resp.json()["kind"] == "pdf"


async def test_member_colleague_receipt_is_403_and_storage_not_called(
    client, db_session, user_a_id, user_b_id, signer
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_a_id)
    resp = await client.get(_url(r.id), headers=_h(user_b_id, t.id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can't view this receipt"
    assert len(signer) == 0


async def test_other_business_receipt_is_404(client, db_session, user_a_id, user_b_id, signer):
    t1 = await _seed_team(db_session, user_a_id, user_b_id, name="One")
    t2 = await _seed_team(db_session, user_a_id, user_b_id, name="Two")
    r = await _receipt(db_session, t2, user_b_id)
    resp = await client.get(_url(r.id), headers=_h(user_a_id, t1.id))
    assert resp.status_code == 404
    assert len(signer) == 0


async def test_unknown_receipt_is_404(client, db_session, user_a_id, user_b_id, signer):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.get(_url(987654), headers=_h(user_a_id, t.id))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Receipt not found"


async def test_out_of_range_id_is_not_a_500(client, db_session, user_a_id, user_b_id, signer):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    resp = await client.get(_url(99999999999999999999), headers=_h(user_a_id, t.id))
    assert resp.status_code == 422


async def test_no_auth_is_401(client, signer):
    resp = await client.get(_url(1))
    assert resp.status_code == 401


async def test_storage_object_missing_is_404_receipt_image_not_found(
    client, db_session, user_a_id, user_b_id, monkeypatch
):
    async def _sign(path, expires_in):
        raise supabase_client.StorageObjectNotFound()

    monkeypatch.setattr(receipts_module.supabase_client, "create_signed_url", _sign)
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.get(_url(r.id), headers=_h(user_a_id, t.id))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Receipt image not found"


async def test_storage_failure_is_502_with_readable_message(
    client, db_session, user_a_id, user_b_id, monkeypatch
):
    async def _sign(path, expires_in):
        raise RuntimeError("boom")

    monkeypatch.setattr(receipts_module.supabase_client, "create_signed_url", _sign)
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.get(_url(r.id), headers=_h(user_a_id, t.id))
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Couldn't load the receipt image. Please try again."


async def test_signed_url_and_token_are_not_logged(
    client, db_session, user_a_id, user_b_id, signer, monkeypatch, caplog
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    caplog.set_level(logging.DEBUG)
    await client.get(_url(r.id), headers=_h(user_a_id, t.id))

    async def _fail(path, expires_in):
        raise RuntimeError(SIGNED)

    monkeypatch.setattr(receipts_module.supabase_client, "create_signed_url", _fail)
    await client.get(_url(r.id), headers=_h(user_a_id, t.id))
    for rec in caplog.records:
        text = rec.getMessage()
        assert "SECRET" not in text and "token=" not in text
        assert "SECRET" not in str(rec.exc_info) and "SECRET" not in (rec.exc_text or "")
