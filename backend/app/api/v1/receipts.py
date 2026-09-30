import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import supabase_client
from app.auth import RequestContext, get_current_context
from app.config import settings
from app.database import get_db
from app.models.fraud_flag import FraudFlag
from app.models.receipt import Receipt
from app.schemas.receipt import ReceiptListResponse, ReceiptResponse, ReviewRequest, ReviewResponse
from app.services.duplicate_detector import compute_image_hash
from app.services.fraud_detection_pipeline import run_fraud_pipeline
from app.services.gemini_explainer import call_gemini
from app.services.ocr import extract_receipt_data

limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/receipts", tags=["receipts"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}


@router.post("/upload", response_model=ReceiptResponse, status_code=201)
async def upload_receipt(
    file: UploadFile,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> Receipt:
    """Upload a receipt image, store it in Supabase Storage, and create a row
    scoped to the caller's active business.

    OCR runs automatically on the uploaded file, followed by the full fraud
    detection pipeline, before the response is returned.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    if len(contents) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    # Write to a temp file so OCR / perceptual hashing (both sync, file-path based)
    # can run against it. The durable copy lives in Supabase Storage, not on disk.
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp_path = tmp.name
    tmp.write(contents)
    tmp.close()

    try:
        # OCR and hash run in a thread pool so they don't block the event loop
        loop = asyncio.get_event_loop()
        ocr, image_hash = await asyncio.gather(
            loop.run_in_executor(None, extract_receipt_data, tmp_path),
            loop.run_in_executor(None, compute_image_hash, tmp_path),
        )

        object_path = f"{context.tenant_id}/{uuid.uuid4().hex}{ext}"
        try:
            await supabase_client.upload_object(object_path, contents, CONTENT_TYPES[ext])
        except Exception as exc:  # storage failure — do not create a row
            logger.error("Storage upload failed for %s: %s", object_path, exc)
            raise HTTPException(status_code=502, detail="File storage failed") from exc

        receipt = Receipt(
            image_path=object_path,
            tenant_id=context.tenant_id,
            submitted_by_user_id=context.user_id,
            status="pending",
        )
        receipt.merchant = ocr.merchant
        receipt.amount = ocr.total_amount
        receipt.transaction_date = ocr.transaction_date
        receipt.items = {
            "line_items": ocr.line_items,
            "transaction_time": ocr.transaction_time,
            "raw_text": ocr.raw_text,
        }
        receipt.ocr_confidence = ocr.confidence
        receipt.ocr_method = "tesseract"
        receipt.analyzed_at = datetime.utcnow()
        receipt.image_hash = image_hash  # may be None if hashing failed

        db.add(receipt)
        try:
            await db.flush()
        except Exception:
            try:
                await supabase_client.delete_object(object_path)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                logger.warning(
                    "Failed to delete orphaned storage object %s after DB error",
                    object_path,
                )
            raise

        await run_fraud_pipeline(receipt, db)
        await db.refresh(receipt, attribute_names=["fraud_flags"])

        logger.info(
            "Created receipt %s for tenant %s (%d bytes)",
            receipt.id, context.tenant_id, len(contents),
        )
        return receipt
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/{receipt_id}/analyze", response_model=ReceiptResponse)
async def analyze_receipt(
    receipt_id: int,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> Receipt:
    """Run the full fraud detection pipeline on an existing receipt.

    Safe to call multiple times — existing fraud flags are cleared before
    each run so results always reflect the current policy rules.

    Returns 400 if the receipt has not been OCR-processed yet (status=pending).
    """
    result = await db.execute(
        select(Receipt)
        .options(selectinload(Receipt.fraud_flags))
        .where(Receipt.id == receipt_id, Receipt.tenant_id == context.tenant_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if receipt.status == "pending":
        raise HTTPException(
            status_code=400,
            detail="Receipt has not been OCR-processed yet; upload again or wait.",
        )

    # Clear flags and cached explanation from any previous run
    await db.execute(delete(FraudFlag).where(FraudFlag.receipt_id == receipt_id))
    receipt.explanation = None

    receipt.analyzed_at = datetime.utcnow()
    await run_fraud_pipeline(receipt, db)

    await db.refresh(receipt, attribute_names=["fraud_flags"])
    return receipt


@router.post("/{receipt_id}/explain")
@limiter.limit("5/day")
async def explain_receipt(
    request: Request,
    receipt_id: int,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a plain-English explanation of the fraud risk score via Gemini 2.5 Flash.

    The result is cached on the receipt record. Subsequent calls return the cached
    value without calling Gemini again. Cleared when the receipt is re-analyzed.
    Rate limited to 5 requests per day per IP.
    """
    result = await db.execute(
        select(Receipt)
        .options(selectinload(Receipt.fraud_flags))
        .where(Receipt.id == receipt_id, Receipt.tenant_id == context.tenant_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    if not receipt.fraud_flags:
        raise HTTPException(status_code=400, detail="Receipt has no fraud flags — nothing to explain")

    if receipt.explanation:
        return {"explanation": receipt.explanation}

    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")

    try:
        explanation = await asyncio.get_event_loop().run_in_executor(
            None, call_gemini, receipt, receipt.fraud_flags
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    receipt.explanation = explanation
    await db.flush()

    logger.info("Cached Gemini explanation for receipt %d", receipt_id)
    return {"explanation": explanation}


@router.patch("/{receipt_id}/review", response_model=ReviewResponse)
async def review_receipt(
    receipt_id: int,
    body: ReviewRequest,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve or reject a receipt after human review.

    Sets status to 'approved' or 'rejected' and records the review timestamp.
    When the receipt has duplicate flags the response includes
    original_receipt_id — the earliest submission with the same image hash —
    so reviewers can distinguish the original from a re-submission.

    Note: the optional review note is logged but not yet persisted
    (requires a schema migration to add a review_note column).
    """
    result = await db.execute(
        select(Receipt)
        .options(selectinload(Receipt.fraud_flags))
        .where(Receipt.id == receipt_id, Receipt.tenant_id == context.tenant_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if receipt.status == "pending":
        raise HTTPException(
            status_code=400,
            detail="Receipt has not been processed yet and cannot be reviewed.",
        )

    receipt.status = body.decision
    receipt.reviewed_at = datetime.utcnow()

    if body.note:
        logger.info("Review note for receipt %d: %s", receipt_id, body.note)

    # Find the earliest receipt with the same hash when duplicate flags exist,
    # so reviewers know whether they are looking at the original or a copy.
    # Scoped to the caller's business — a duplicate chain never crosses tenants.
    original_receipt_id: int | None = None
    has_duplicate_flag = any(f.flag_type == "duplicate" for f in receipt.fraud_flags)
    if has_duplicate_flag and receipt.image_hash:
        earliest = await db.execute(
            select(Receipt.id)
            .where(Receipt.image_hash == receipt.image_hash)
            .where(Receipt.tenant_id == context.tenant_id)
            .where(Receipt.id != receipt_id)
            .order_by(Receipt.created_at.asc())
            .limit(1)
        )
        original_receipt_id = earliest.scalar_one_or_none()

    await db.flush()

    logger.info(
        "Receipt %d %s by reviewer (original_receipt_id=%s)",
        receipt_id, body.decision, original_receipt_id,
    )

    response_data = ReceiptResponse.model_validate(receipt).model_dump()
    response_data["original_receipt_id"] = original_receipt_id
    return response_data


@router.get("/{receipt_id}", response_model=ReceiptResponse)
async def get_receipt(
    receipt_id: int,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> Receipt:
    """Retrieve a receipt by ID within the caller's active business."""
    result = await db.execute(
        select(Receipt)
        .options(selectinload(Receipt.fraud_flags))
        .where(Receipt.id == receipt_id, Receipt.tenant_id == context.tenant_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


@router.get("", response_model=ReceiptListResponse)
async def list_receipts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str | None = None,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List receipts for the caller's active business."""
    query = (
        select(Receipt)
        .options(selectinload(Receipt.fraud_flags))
        .where(Receipt.tenant_id == context.tenant_id)
    )
    count_query = select(func.count(Receipt.id)).where(
        Receipt.tenant_id == context.tenant_id
    )

    if status:
        query = query.where(Receipt.status == status)
        count_query = count_query.where(Receipt.status == status)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * per_page
    query = query.order_by(Receipt.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    receipts = list(result.scalars().all())

    return {
        "receipts": receipts,
        "total": total,
        "page": page,
        "per_page": per_page,
    }
