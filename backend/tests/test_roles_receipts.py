import io
import uuid

import pytest
from sqlalchemy import select

from app import supabase_client
from app.api.v1 import receipts as receipts_module
from app.models import Membership, Receipt, Tenant
from app.models.fraud_flag import FraudFlag
from app.services.ocr import OCRResult
from tests.conftest import make_token


@pytest.fixture(autouse=True)
def _mock_upload_deps(monkeypatch):
    async def _upload(path, data, content_type):
        return None

    async def _delete(path):
        return None

    async def _pipeline(receipt, db):
        return None

    monkeypatch.setattr(supabase_client, "upload_object", _upload)
    monkeypatch.setattr(supabase_client, "delete_object", _delete)
    monkeypatch.setattr(receipts_module, "extract_receipt_data", lambda path: OCRResult())
    monkeypatch.setattr(receipts_module, "compute_image_hash", lambda path: "0" * 16)
    monkeypatch.setattr(receipts_module, "run_fraud_pipeline", _pipeline)


def _png() -> dict:
    return {"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "image/png")}


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


async def _receipt(db_session, tenant, user_id, status="analyzed"):
    r = Receipt(
        image_path="x.png", tenant_id=tenant.id,
        submitted_by_user_id=uuid.UUID(user_id), status=status,
    )
    db_session.add(r)
    await db_session.commit()
    return r


async def _dup_flag(db_session, receipt, target_id):
    f = FraudFlag(
        receipt_id=receipt.id, flag_type="duplicate", severity="high",
        description="dup", details={"duplicate_receipt_id": target_id, "other": 1},
        confidence_score=100,
    )
    db_session.add(f)
    await db_session.commit()
    return f


async def test_member_list_shows_only_own_and_total_is_own_count(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    mine = await _receipt(db_session, t, user_b_id)
    await _receipt(db_session, t, user_a_id)
    await _receipt(db_session, t, user_a_id)
    resp = await client.get("/api/v1/receipts", headers=_h(user_b_id, t.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [r["id"] for r in body["receipts"]] == [mine.id]


async def test_admin_list_shows_all_in_business(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    await _receipt(db_session, t, user_b_id)
    await _receipt(db_session, t, user_a_id)
    resp = await client.get("/api/v1/receipts", headers=_h(user_a_id, t.id))
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert len(resp.json()["receipts"]) == 2


async def test_member_get_own_receipt_ok(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.get(f"/api/v1/receipts/{r.id}", headers=_h(user_b_id, t.id))
    assert resp.status_code == 200
    assert resp.json()["id"] == r.id


async def test_member_get_colleague_receipt_is_403_cant_view(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_a_id)
    resp = await client.get(f"/api/v1/receipts/{r.id}", headers=_h(user_b_id, t.id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can't view this receipt"


async def test_get_other_business_receipt_is_404_for_member_and_admin(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed_team(db_session, user_a_id, user_b_id, "AceCo")
    other_owner = str(uuid.uuid4())
    other = Tenant(name="BeeCo", created_by_user_id=uuid.UUID(other_owner))
    db_session.add(other)
    await db_session.flush()
    db_session.add(Membership(tenant_id=other.id, user_id=uuid.UUID(other_owner), role="admin"))
    await db_session.commit()
    r = await _receipt(db_session, other, other_owner)
    for uid in (user_a_id, user_b_id):
        resp = await client.get(f"/api/v1/receipts/{r.id}", headers=_h(uid, t.id))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Receipt not found"


async def test_member_cannot_review(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.request(
        "PATCH", f"/api/v1/receipts/{r.id}/review",
        json={"decision": "approved"}, headers=_h(user_b_id, t.id),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only admins can approve or reject receipts"


async def test_member_cannot_analyze(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.post(f"/api/v1/receipts/{r.id}/analyze", headers=_h(user_b_id, t.id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only admins can re-run fraud analysis"


async def test_admin_can_review_and_analyze(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_b_id)
    resp = await client.post(f"/api/v1/receipts/{r.id}/analyze", headers=_h(user_a_id, t.id))
    assert resp.status_code == 200
    resp = await client.request(
        "PATCH", f"/api/v1/receipts/{r.id}/review",
        json={"decision": "approved"}, headers=_h(user_a_id, t.id),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


async def test_member_explain_colleague_receipt_403_before_gemini(
    client, db_session, user_a_id, user_b_id, monkeypatch
):
    def _boom(*a, **k):
        pytest.fail("call_gemini must not be called")

    monkeypatch.setattr(receipts_module, "call_gemini", _boom)
    monkeypatch.setattr(receipts_module.settings, "GEMINI_API_KEY", "k", raising=False)
    t = await _seed_team(db_session, user_a_id, user_b_id)
    r = await _receipt(db_session, t, user_a_id)
    await _dup_flag(db_session, r, 999)
    resp = await client.post(f"/api/v1/receipts/{r.id}/explain", headers=_h(user_b_id, t.id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can't view this receipt"


async def test_member_duplicate_flag_details_redacted_for_colleague_receipt(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    colleague = await _receipt(db_session, t, user_a_id)
    mine = await _receipt(db_session, t, user_b_id)
    await _dup_flag(db_session, mine, colleague.id)
    resp = await client.get(f"/api/v1/receipts/{mine.id}", headers=_h(user_b_id, t.id))
    assert resp.status_code == 200
    details = resp.json()["fraud_flags"][0]["details"]
    assert "duplicate_receipt_id" not in details
    assert details["other"] == 1

    resp = await client.get("/api/v1/receipts", headers=_h(user_b_id, t.id))
    assert "duplicate_receipt_id" not in resp.json()["receipts"][0]["fraud_flags"][0]["details"]


async def test_member_duplicate_flag_keeps_pointer_to_own_receipt(
    client, db_session, user_a_id, user_b_id
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    first = await _receipt(db_session, t, user_b_id)
    second = await _receipt(db_session, t, user_b_id)
    await _dup_flag(db_session, second, first.id)
    resp = await client.get(f"/api/v1/receipts/{second.id}", headers=_h(user_b_id, t.id))
    assert resp.json()["fraud_flags"][0]["details"]["duplicate_receipt_id"] == first.id


async def test_redaction_does_not_modify_database_row(client, db_session, user_a_id, user_b_id):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    colleague = await _receipt(db_session, t, user_a_id)
    mine = await _receipt(db_session, t, user_b_id)
    flag = await _dup_flag(db_session, mine, colleague.id)
    flag_id, colleague_id = flag.id, colleague.id
    resp = await client.get(f"/api/v1/receipts/{mine.id}", headers=_h(user_b_id, t.id))
    assert "duplicate_receipt_id" not in resp.json()["fraud_flags"][0]["details"]
    await db_session.commit()
    db_session.expire_all()
    row = (await db_session.execute(select(FraudFlag).where(FraudFlag.id == flag_id))).scalar_one()
    assert row.details["duplicate_receipt_id"] == colleague_id


async def test_member_upload_still_works_and_response_is_present_redacted(
    client, db_session, user_a_id, user_b_id, monkeypatch
):
    t = await _seed_team(db_session, user_a_id, user_b_id)
    colleague = await _receipt(db_session, t, user_a_id)

    async def _pipeline(receipt, db):
        db.add(FraudFlag(
            receipt_id=receipt.id, flag_type="duplicate", severity="high",
            description="dup", details={"duplicate_receipt_id": colleague.id},
            confidence_score=100,
        ))
        await db.flush()

    monkeypatch.setattr(receipts_module, "run_fraud_pipeline", _pipeline)
    resp = await client.post("/api/v1/receipts/upload", files=_png(), headers=_h(user_b_id, t.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["submitted_by_user_id"] == user_b_id
    assert len(body["fraud_flags"]) == 1
    assert "duplicate_receipt_id" not in body["fraud_flags"][0]["details"]
