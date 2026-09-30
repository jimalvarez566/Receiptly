import io
import uuid

import pytest

from app import supabase_client
from app.api.v1 import receipts as receipts_module
from app.models import Membership, Receipt, Tenant
from app.services.ocr import OCRResult
from tests.conftest import make_token


@pytest.fixture(autouse=True)
def _mock_upload_deps(monkeypatch):
    """Stub out Storage + OCR + hashing + the fraud pipeline for upload tests."""
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


async def _seed(db_session, owner_id, name):
    tenant = Tenant(name=name, created_by_user_id=owner_id)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=owner_id, role="owner"))
    await db_session.commit()
    return tenant


def _png() -> dict:
    return {"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "image/png")}


async def test_upload_stamps_tenant_and_submitter(client, db_session, user_a_id):
    tenant = await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/receipts/upload",
        files=_png(),
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant_id"] == tenant.id
    assert body["submitted_by_user_id"] == user_a_id
    assert body["image_path"].startswith(f"{tenant.id}/")


async def test_list_only_returns_active_tenant(client, db_session, user_a_id, user_b_id):
    ace = await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    bee = await _seed(db_session, uuid.UUID(user_b_id), "BeeCo")
    db_session.add(Receipt(
        image_path="x", tenant_id=bee.id,
        submitted_by_user_id=uuid.UUID(user_b_id), status="pending",
    ))
    await db_session.commit()

    token = make_token(user_a_id)
    resp = await client.get(
        "/api/v1/receipts",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(ace.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_get_other_tenant_receipt_is_404(client, db_session, user_a_id, user_b_id):
    ace = await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    bee = await _seed(db_session, uuid.UUID(user_b_id), "BeeCo")
    other = Receipt(
        image_path="x", tenant_id=bee.id,
        submitted_by_user_id=uuid.UUID(user_b_id), status="pending",
    )
    db_session.add(other)
    await db_session.commit()

    token = make_token(user_a_id)
    resp = await client.get(
        f"/api/v1/receipts/{other.id}",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(ace.id)},
    )
    assert resp.status_code == 404


async def test_upload_without_tenant_header_is_400(client, db_session, user_a_id):
    await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/receipts/upload", files=_png(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


from app.models.fraud_flag import FraudFlag

_SHARED_HASH = "a" * 16


async def test_review_original_receipt_id_is_tenant_scoped(client, db_session, user_a_id, user_b_id):
    ace = await _seed(db_session, uuid.UUID(user_a_id), "AceCo")
    bee = await _seed(db_session, uuid.UUID(user_b_id), "BeeCo")

    # Tenant A already has a receipt with the shared image hash.
    a_receipt = Receipt(
        image_path="a/x.png", tenant_id=ace.id,
        submitted_by_user_id=uuid.UUID(user_a_id),
        status="analyzed", image_hash=_SHARED_HASH,
    )
    db_session.add(a_receipt)
    await db_session.flush()

    # Tenant B's receipt: same hash, already analyzed, carries a duplicate flag.
    b_receipt = Receipt(
        image_path="b/x.png", tenant_id=bee.id,
        submitted_by_user_id=uuid.UUID(user_b_id),
        status="flagged", image_hash=_SHARED_HASH,
    )
    db_session.add(b_receipt)
    await db_session.flush()
    db_session.add(FraudFlag(
        receipt_id=b_receipt.id, flag_type="duplicate", severity="high",
        description="dup", details={}, confidence_score=100,
    ))
    await db_session.commit()

    token = make_token(user_b_id)
    resp = await client.request(
        "PATCH",
        f"/api/v1/receipts/{b_receipt.id}/review",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(bee.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["original_receipt_id"] is None  # A's receipt must be invisible
