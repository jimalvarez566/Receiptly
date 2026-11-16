# API Reference

Base URL: `http://localhost:8000`  
All receipt endpoints are prefixed with `/api/v1/receipts`.

---

## Health

### `GET /health`

Returns server status.

**Response `200`**
```json
{ "status": "ok" }
```

---

## Receipts

### `POST /api/v1/receipts/upload`

Upload a receipt image and run the full fraud detection pipeline.

**Request** — `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | file | yes | PNG, JPG, JPEG, or PDF. Max 10 MB. |
| `employee_id` | integer | no | Associate with an employee record. |

**Response `201`** — `ReceiptResponse`

**Errors**
- `400` — missing filename, unsupported file type, or file too large

---

### `POST /api/v1/receipts/{id}/analyze`

Re-run the fraud detection pipeline on an existing receipt. Clears previous fraud flags and the cached Gemini explanation before running.

**Path params**

| Param | Type | Description |
|---|---|---|
| `id` | integer | Receipt ID |

**Response `200`** — `ReceiptResponse`

**Errors**
- `400` — receipt has not been OCR-processed yet (status = `pending`)
- `404` — receipt not found

---

### `POST /api/v1/receipts/{id}/explain`

Generate a plain-English explanation of the fraud risk score using Gemini 2.5 Flash. Result is cached on the receipt record — subsequent calls return the cached value without calling Gemini again.

**Rate limit:** 5 requests per day per IP.

**Path params**

| Param | Type | Description |
|---|---|---|
| `id` | integer | Receipt ID |

**Response `200`**
```json
{
  "explanation": "This receipt was flagged because..."
}
```

**Errors**
- `400` — receipt has no fraud flags (nothing to explain)
- `404` — receipt not found
- `429` — rate limit exceeded (5/day per IP)
- `502` — Gemini API call failed
- `503` — `GEMINI_API_KEY` not configured

---

### `PATCH /api/v1/receipts/{id}/review`

Approve or reject a receipt after human review.

**Path params**

| Param | Type | Description |
|---|---|---|
| `id` | integer | Receipt ID |

**Request body**
```json
{
  "decision": "approved",
  "note": "Optional reviewer note"
}
```

| Field | Type | Required | Values |
|---|---|---|---|
| `decision` | string | yes | `"approved"` or `"rejected"` |
| `note` | string | no | Free-text reviewer note (logged, not persisted) |

**Response `200`** — `ReviewResponse` (same as `ReceiptResponse` plus `original_receipt_id`)

`original_receipt_id` is set when the receipt has a duplicate flag — it identifies the earliest submission with the same image hash.

**Errors**
- `400` — receipt status is `pending`
- `404` — receipt not found

---

### `GET /api/v1/receipts/{id}`

Fetch a single receipt including its fraud flags.

**Path params**

| Param | Type | Description |
|---|---|---|
| `id` | integer | Receipt ID |

**Response `200`** — `ReceiptResponse`

**Errors**
- `404` — receipt not found

---

### `GET /api/v1/receipts`

List receipts with pagination and optional status filter.

**Query params**

| Param | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number (≥ 1) |
| `per_page` | integer | `20` | Items per page (1–100) |
| `status` | string | — | Filter by status: `pending`, `flagged`, `approved`, `rejected` |

**Response `200`**
```json
{
  "receipts": [ ...ReceiptResponse ],
  "total": 47,
  "page": 1,
  "per_page": 20
}
```

---

## Schemas

### `ReceiptResponse`

```json
{
  "id": 1,
  "employee_id": null,
  "image_path": "./uploads/abc123.jpg",
  "image_hash": "f8a1...",
  "merchant": "Starbucks",
  "amount": "12.50",
  "transaction_date": "2026-03-15",
  "category": null,
  "items": {
    "line_items": [{ "description": "Latte", "amount": 6.50 }],
    "transaction_time": "08:42:00",
    "raw_text": "..."
  },
  "ocr_confidence": "75.00",
  "ocr_method": "paddle",
  "fraud_score": 42,
  "risk_level": "medium",
  "status": "flagged",
  "explanation": null,
  "created_at": "2026-03-15T10:00:00",
  "analyzed_at": "2026-03-15T10:00:05",
  "reviewed_at": null,
  "fraud_flags": [ ...FraudFlagResponse ]
}
```

### `FraudFlagResponse`

```json
{
  "id": 1,
  "flag_type": "duplicate",
  "severity": "high",
  "description": "Receipt appears to be a duplicate of receipt #3",
  "details": { "original_receipt_id": 3, "hamming_distance": 4 },
  "confidence_score": "0.95",
  "created_at": "2026-03-15T10:00:05"
}
```

### `ReviewResponse`

Same as `ReceiptResponse` plus:

```json
{
  "original_receipt_id": 3
}
```

---

## Risk Levels

| Score | Level |
|---|---|
| 0–29 | `low` |
| 30–69 | `medium` |
| 70–100 | `high` |

## Receipt Statuses

| Status | Meaning |
|---|---|
| `pending` | Uploaded but not yet processed |
| `flagged` | Processed — one or more fraud flags detected |
| `approved` | Reviewed and approved by a human |
| `rejected` | Reviewed and rejected by a human |
