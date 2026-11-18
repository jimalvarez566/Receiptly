# Signed Receipt-Image URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the original receipt file on the receipt detail page via a short-lived signed Supabase Storage link, handed out only to people allowed to view that receipt.

**Architecture:** A new Storage helper signs a link with the service-role key. A new `GET /receipts/{id}/image-url` endpoint reuses the receipt's own visibility check (`_get_receipt_for`) and returns `{url, expires_in, kind}` with `Cache-Control: no-store`. A new `ReceiptImage` frontend component fetches the link, renders an image or PDF viewer, and refetches once if the link has expired.

**Tech Stack:** FastAPI, httpx (`supabase_client._client()` seam), pytest-asyncio; React 19 + TS strict + Tailwind (no frontend test runner).

**Spec:** `docs/superpowers/specs/2026-11-18-signed-image-urls-design.md`

## Global Constraints

- Endpoint: `GET /api/v1/receipts/{receipt_id}/image-url` → `{"url": str, "expires_in": int, "kind": "image" | "pdf"}`; `kind` is `"pdf"` when the stored `image_path` ends in `.pdf` (case-insensitive), else `"image"`; response header `Cache-Control: no-store`.
- Permission = the receipt's own: admin any receipt in the business; member only their own (403 `"You can't view this receipt"`); another business's receipt or unknown id → 404 `"Receipt not found"`; no/invalid login → 401. Reuse `_get_receipt_for`; no new permission logic.
- Errors: file missing in Storage → 404 `"Receipt image not found"`; any other Storage failure → 502 `"Couldn't load the receipt image. Try again."`. Signed URLs and tokens are never logged (no `logger` call may include the URL or `token=`).
- Expiry: `RECEIPT_IMAGE_URL_TTL_SECONDS: int = 300` in `app/config.py`; the endpoint passes it to signing and returns it as `expires_in`.
- Signing request: `POST {SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_STORAGE_BUCKET}/{path}` with JSON `{"expiresIn": <int>}` through the existing `_client()` seam (service-role headers); the returned URL is absolute: `{SUPABASE_URL.rstrip('/')}/storage/v1` + the response's `signedURL` (which starts with `/object/sign/...?token=...`).
- No migration, no new dependency, no change to `image_path` in `ReceiptResponse`, no thumbnails/list changes.
- Frontend: only the detail page shows the file; lint must show no errors beyond the 5 known (TenantProvider.tsx, ReceiptDetail.tsx ×2, ReceiptsList.tsx, ui/button.tsx react-refresh); no synchronous `setState` at the top of an effect; keep non-component exports out of component files.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never run alembic or any DB command against `backend/.env`'s database; backend tests use the local Postgres only; Task 3's live Storage check needs the user's explicit yes (controller asks).

## Review Focus

- A member requesting a colleague's receipt link gets 403 **before** Storage is contacted (assert the signing function is not called) (Task 2 test).
- Path handling: an `image_path` containing spaces/special characters is URL-encoded in the signing request but not double-encoded; signing never uses a path from the request, only the DB row (Task 1 test).
- The Storage response is malformed (missing `signedURL`, non-JSON, or a 200 with an error body) → 502, never a 500 (Task 1 test).
- Unknown/huge receipt id → 404/422 not 500 (same behavior as the other receipt routes; Task 2 test with an out-of-int32-range id must not 500 — constrain the path param like the policy-rules routes, `Path(ge=1, le=2**31-1)`).
- The signed URL/token does not appear in log output when signing succeeds or fails (Task 2 test with `caplog`).
- The component never shows a blank frame: loading, error + Retry, and the one-time refetch on load failure are all reachable states (Task 3 verification).

---

## File Structure

- Modify `backend/app/config.py`, `backend/app/supabase_client.py`, `backend/app/api/v1/receipts.py`, `backend/app/schemas/receipt.py`.
- Tests: extend `backend/tests/test_supabase_client.py`; create `backend/tests/test_receipt_image_url.py`.
- Frontend: modify `frontend/src/lib/api.ts`, `frontend/src/pages/ReceiptDetail.tsx`; create `frontend/src/components/ReceiptImage.tsx`.
- Docs: `README.md`, `TODO.md`.

---

### Task 1: Storage signing helper and config

**Files:**
- Modify: `backend/app/config.py`, `backend/app/supabase_client.py`
- Test: `backend/tests/test_supabase_client.py` (extend, reuse its `mock_transport` pattern — add a separate fixture/handler for the sign route)

**Interfaces:**
- Produces:
  - `settings.RECEIPT_IMAGE_URL_TTL_SECONDS: int = 300`
  - `class StorageObjectNotFound(Exception)` in `app/supabase_client.py`
  - `async def create_signed_url(path: str, expires_in: int) -> str` — returns the absolute signed URL; raises `StorageObjectNotFound` when Storage reports the object missing (HTTP 404, or HTTP 400 with an error body naming "not found"); raises `RuntimeError` for any other non-2xx, non-JSON body, or a body without a string `signedURL`.

- [ ] **Step 1: Write failing tests** (async, mocked transport, same style as the existing tests): `test_create_signed_url_posts_expected_request_and_returns_absolute_url` (asserts method POST, path `/storage/v1/object/sign/receipts/1/abc.png`, JSON body `{"expiresIn": 300}`, service-role `Authorization` header present, and result equals `{SUPABASE_URL}/storage/v1/object/sign/receipts/1/abc.png?token=T`); `test_create_signed_url_encodes_special_characters_once` (path `1/my receipt #1.png` → request path contains `my%20receipt%20%231.png`, never `%2520`); `test_create_signed_url_missing_object_raises_not_found` (404 and the 400 "Object not found" body variants); `test_create_signed_url_other_failure_raises_runtime_error` (500); `test_create_signed_url_malformed_body_raises_runtime_error` (non-JSON; JSON without `signedURL`; `signedURL` not a string).
- [ ] **Step 2: Run** `cd backend && python3 -m pytest tests/test_supabase_client.py -q` — expect the new tests to FAIL (name missing).
- [ ] **Step 3: Implement** the setting, `StorageObjectNotFound`, and `create_signed_url` in `supabase_client.py` following `upload_object`'s style (use `quote(path, safe="/")` for the path; never log the URL or response body).
- [ ] **Step 4: Run** the file — PASS; then the full backend suite — green.
- [ ] **Step 5: Commit** `feat: sign short-lived Storage URLs for receipt files`.

### Task 2: Image-URL endpoint

**Files:**
- Modify: `backend/app/api/v1/receipts.py`, `backend/app/schemas/receipt.py`
- Test: `backend/tests/test_receipt_image_url.py`

**Interfaces:**
- Consumes: `_get_receipt_for(context, receipt_id, db)` and `get_current_context` (already in `receipts.py`); `supabase_client.create_signed_url`, `StorageObjectNotFound` (Task 1); `settings.RECEIPT_IMAGE_URL_TTL_SECONDS`.
- Produces: `class ReceiptImageUrl(BaseModel)` in `app/schemas/receipt.py` with `url: str`, `expires_in: int`, `kind: Literal["image", "pdf"]`; route `GET /{receipt_id}/image-url` on the existing receipts router, `response_model=ReceiptImageUrl`, `receipt_id` path param `Annotated[int, Path(ge=1, le=2**31 - 1)]`, sets `Cache-Control: no-store` via a `Response` parameter.

- [ ] **Step 1: Write failing tests** in `test_receipt_image_url.py` (follow `tests/test_roles_receipts.py`: `_seed_team`, `_receipt` helpers — reproduce what you need locally; monkeypatch `receipts_module.supabase_client.create_signed_url` with a recording async stub that returns `"https://signed.example/x?token=SECRET"`):
  `test_admin_gets_url_for_any_receipt_in_business` (200; body `url`/`expires_in == 300`/`kind == "image"`; header `cache-control == "no-store"`; the stub was called with the receipt's `image_path` and 300); `test_member_gets_url_for_own_receipt`; `test_pdf_receipt_has_kind_pdf` (path `1/a.PDF`); `test_member_colleague_receipt_is_403_and_storage_not_called` (exact `"You can't view this receipt"`, stub call count 0); `test_other_business_receipt_is_404`; `test_unknown_receipt_is_404`; `test_out_of_range_id_is_not_a_500` (`99999999999999999999` → 422); `test_no_auth_is_401`; `test_storage_object_missing_is_404_receipt_image_not_found`; `test_storage_failure_is_502_with_readable_message`; `test_signed_url_and_token_are_not_logged` (`caplog` at DEBUG for success and failure paths: no record contains `SECRET` or `token=`).
- [ ] **Step 2: Run** `python3 -m pytest tests/test_receipt_image_url.py -q` — FAIL (404 for the missing route).
- [ ] **Step 3: Implement** the schema and route: load via `_get_receipt_for`, compute `kind` from `receipt.image_path`, call `supabase_client.create_signed_url(receipt.image_path, settings.RECEIPT_IMAGE_URL_TTL_SECONDS)` inside try/except mapping `StorageObjectNotFound` → 404 and any other `Exception` → 502 (log only the receipt id and exception type, never the URL).
- [ ] **Step 4: Run** the file — PASS; then the full suite — green.
- [ ] **Step 5: Commit** `feat: endpoint returning a short-lived signed URL for a receipt file`.

### Task 3: Frontend display, docs, and verification

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/pages/ReceiptDetail.tsx`, `README.md`, `TODO.md`
- Create: `frontend/src/components/ReceiptImage.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/receipts/{id}/image-url` (Task 2); existing `request()`/`ApiError` in `api.ts`.
- Produces: `export interface ReceiptImageUrl { url: string; expires_in: number; kind: 'image' | 'pdf' }`; `api.getReceiptImageUrl(id: number): Promise<ReceiptImageUrl>`; `export function ReceiptImage(props: { receiptId: number }): JSX.Element`.

- [ ] **Step 1: Implement** the API method/type and `ReceiptImage`: on mount (and when `receiptId` changes) fetch the link without synchronous setState at the top of the effect; states: loading ("Loading image…"), image (`<img>` constrained to the card width with `alt="Receipt"`), pdf (`<object type="application/pdf" data=url>` with a fixed height plus an "Open in new tab" link `target="_blank" rel="noopener noreferrer"`), error (the server message from `err.message`, or "Couldn't load the receipt image." + a Retry button). When the `<img>` `onError` fires (or the `<object>` fails), refetch a fresh link **once** automatically; a second failure shows the error state. Cancel/ignore results of stale requests when `receiptId` changes.
- [ ] **Step 2: Render** `<ReceiptImage receiptId={receipt.id} />` in `ReceiptDetail.tsx` between the header block and the detail fields (do not add effects or state to `ReceiptDetail.tsx` itself).
- [ ] **Step 3: Docs.** README: a short "Viewing receipt images" note — links expire after 5 minutes (configurable via `RECEIPT_IMAGE_URL_TTL_SECONDS`), only people allowed to see the receipt get a link, and the caveat that expiry stops new loads but does not recall a file a browser already loaded (delete the Storage object to cut a file off). TODO.md: tick the signed-URL item.
- [ ] **Step 4: Verify.** `cd frontend && npm run build` passes; `npm run lint` shows only the 5 known errors; `cd ../backend && python3 -m pytest -q` green.
- [ ] **Step 5: Local end-to-end (no Supabase).** Using a throwaway localhost DB `receiptly_img_e2e` (alembic with BOTH `DATABASE_URL` and `DATABASE_URL_DIRECT` set to it; abort unless host is localhost), a local launcher that stubs Storage upload/delete/**signing** (return a fake `https://signed.example/...?token=...` URL) and the email lookup, minted HS256 tokens: as admin upload a PNG and a PDF receipt → `image-url` returns `kind` `image`/`pdf`, `expires_in` 300, `Cache-Control: no-store`; as member upload one receipt and request both the own receipt (200) and the admin's (403 "You can't view this receipt"); another business's receipt → 404; a stubbed "object missing" → 404 and stubbed failure → 502. Stop the server and drop the database afterwards; keep scripts out of git.
- [ ] **Step 6: Commit** `feat(frontend): show receipt image on the detail page` (docs may be a second commit `docs: describe receipt image links`).
- [ ] **Step 7 (needs the user's yes, run by the controller):** one real check against the live project's Storage: sign a link for an existing receipt object with `create_signed_url`, `GET` it (expect 200 and image bytes), then `GET` the same object URL without the token (expect refusal); delete nothing. Then push the branch and open the PR against `main` (also needs the user's yes).
