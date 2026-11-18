# Short-Lived Signed Receipt-Image URLs — Design (short)

**Date:** 2026-11-18
**Branch:** `feature/signed-image-urls` (from `main` @ d083f3b)
**Status:** Draft for approval

## Intent
Receipt photos are uploaded to a private Supabase Storage bucket
(`receipts/{business_id}/{random}.{png|jpg|jpeg|pdf}`) but nothing can display
them, so an admin deciding on a flagged receipt can't see what was submitted.
Show the original file on the receipt detail page, using links that expire
quickly and are only handed to people allowed to view that receipt.

## Decisions (from the user)
- Only the **receipt detail page** shows the image. **No thumbnails** in lists.
- Approach **A**: the backend returns a short-lived signed link from Supabase
  Storage; the browser loads the file straight from Supabase.

## Approaches considered
- **A. Signed link (chosen):** new endpoint returns a time-limited Storage URL.
  Good: matches the backlog item, no image bytes through our server. Trade-off:
  whoever holds the link can open that one file until it expires.
- **B. Backend streams the file:** no shareable link, but every image load
  passes through our server and the page must fetch with a login header and
  build a local blob. Not chosen.
- **C. Public bucket:** permanent links, bypasses RLS/business separation.
  Rejected.

## Goals / non-goals
**Goals:** endpoint with exactly the receipt's own view permission; short expiry;
image and PDF display on the detail page; readable errors.
**Non-goals:** thumbnails or batch signing, downloads/renames, revoking issued
links, image processing/resizing, removing `image_path` from the receipt
response (possible follow-up: it exposes the internal storage path).

## Calls I made (flag any you disagree with)
1. **Expiry 300 seconds**, configurable via `RECEIPT_IMAGE_URL_TTL_SECONDS`
   (default 300).
2. **Expiry limits who can start loading a file, not what a browser has already
   loaded.** Supabase caches files in browsers (and on a CDN on paid plans) for
   a while after a link expires; cutting off a file entirely means deleting the
   object. Acceptable for this project; stated in the README.
3. **Same permission as the receipt:** admins any receipt in their business;
   members only their own (403 "You can't view this receipt"); another
   business's receipt is a 404. Implemented by reusing the existing
   `_get_receipt_for` helper.
4. **Response:** `GET /api/v1/receipts/{id}/image-url` ->
   `{"url": "...", "expires_in": 300, "kind": "image" | "pdf"}`, where `kind`
   comes from the stored file's extension. Sent with `Cache-Control: no-store`.
   Links/tokens are never logged.
5. **Errors:** the file is missing in Storage -> 404 "Receipt image not found";
   any other Storage failure -> 502 "Couldn't load the receipt image. Try
   again."
6. **Display:** a new `ReceiptImage` component at the top of the detail page:
   `<img>` for images; for PDFs an embedded viewer plus an "Open in new tab"
   link. If the file fails to load (link expired while the page sat open) it
   fetches a fresh link once, then shows a readable message with a Retry
   button. The component is a new file so the existing lint errors in
   `ReceiptDetail.tsx` don't grow.
7. **No rate limit** on the endpoint (it is authenticated and tenant-scoped).
8. No migration, no new dependency. Images already live under the configured
   bucket (`SUPABASE_STORAGE_BUCKET`).

## Backend
- `app/supabase_client.py`: `async def create_signed_url(path: str, expires_in: int) -> str`
  -> `POST {SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}` with JSON
  `{"expiresIn": expires_in}` using the existing service-role `_client()`;
  returns the absolute URL (`{SUPABASE_URL}/storage/v1` + the returned
  `signedURL`). Missing object -> a distinct error (e.g. `StorageObjectNotFound`);
  other non-2xx -> `RuntimeError`, like `upload_object`. Uses the `_client()`
  seam so tests mock it.
- `app/config.py`: `RECEIPT_IMAGE_URL_TTL_SECONDS: int = 300`.
- `app/api/v1/receipts.py`: the endpoint, placed before `/{receipt_id}`-style
  catch routes as needed; response schema `ReceiptImageUrl`.

## Frontend
`api.getReceiptImageUrl(id)`; `ReceiptImage` component; rendered in
`ReceiptDetail.tsx` above the details. Build + lint (no new errors).

## Tests
- Signing client with a mocked transport: request URL/body/headers, absolute-URL
  composition, not-found and failure mapping.
- Endpoint: admin and member-own -> 200 with `kind` for png/jpg/pdf and
  `no-store`; member on a colleague's receipt -> 403 exact message;
  other business -> 404; no auth -> 401; storage missing -> 404; storage
  failure -> 502 with the readable message; the signed URL never appears in
  logs.
- Local end-to-end on a throwaway localhost DB (Storage stubbed), and, with the
  user's OK, one real signing check against the project's Storage: sign an
  existing receipt object, fetch it (200), and confirm the same path without
  the token is refused.

## Rollout
No migration. Deploy backend + frontend together. Nothing changes for stored files.
