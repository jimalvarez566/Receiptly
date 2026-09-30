# AI-Powered Expense Fraud Detection

Senior capstone project (CPSC 490/491) — an intelligent system that analyzes employee expense receipts to identify fraudulent submissions using computer vision, NLP, and machine learning.

## Stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy async, PostgreSQL
- **AI/ML:** Tesseract OCR, imagehash (pHash), Gemini 2.5 Flash, scikit-learn (Phase 2)
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, React Router

## What's Built

### Backend — Fully Functional Fraud Detection Pipeline

Every receipt upload automatically runs the full pipeline:

```
Upload → OCR + pHash (parallel) → Duplicate Detection → Policy Validation → Fraud Scoring → Response
```

### OCR Service (`app/services/ocr.py`)
Extracts structured data from receipt images using Tesseract with a preprocessing pipeline:

| Field | Method |
|---|---|
| Merchant name | Largest-font line via Tesseract level-4 bounding boxes, boilerplate-filtered, top-60% zone |
| Transaction date | Regex across MM/DD/YYYY, YYYY-MM-DD, and month-name formats |
| Total amount | Keyword-anchored search (`total`, `amount due`), falls back to largest dollar figure |
| Line items | Per-line regex, stops at first footer keyword (subtotal/tax/total) |

Preprocessing: grayscale → upscale (min 1000px) → autocontrast → sharpen → binarize.

### Fraud Detection Services

**Duplicate Detector** (`app/services/duplicate_detector.py`)
- Computes a perceptual hash (pHash) of each uploaded image
- Compares against all existing hashes using Hamming distance (threshold ≤ 10/64 bits)
- Flags near-identical images even when re-photographed at different angles or brightness

**Policy Validator** (`app/services/policy_validator.py`)
- Evaluates all active `policy_rules` from the database
- Five rule types: `amount_limit`, `future_date`, `vendor_category`, `round_number`, `short_window_duplicate`
- Businesses created via `POST /api/v1/tenants` get the six default policy rules
  automatically; use `python seed_policies.py --tenant-id <id>` only to backfill a
  business created before that behavior existed.

**Fraud Scorer** (`app/services/fraud_scorer.py`)
- Combines all flags into a 0–100 risk score
- Weights by flag type and severity; diminishing returns for multiple flags
- Risk levels: `low` (0–29), `medium` (30–69), `high` (70–100)

**Pipeline Orchestrator** (`app/services/fraud_detection_pipeline.py`)
- Single `run_fraud_pipeline(receipt, db)` used by both upload and analyze endpoints
- Adding Phase 2 detectors requires one new call here

### Frontend — Prototype UI ✅

Dark fintech SaaS interface (Stripe/Linear aesthetic). Run locally with `npm run dev` from `frontend/`.

| Page | Route | Description |
|---|---|---|
| Dashboard | `/` | Summary cards — total, pending, approved, rejected, risk breakdown |
| Receipts List | `/receipts` | Paginated table with status filter; click any row to open detail |
| Receipt Detail | `/receipts/:id` | Fraud score, OCR fields, line items, fraud flags, approve/reject/re-analyze, "Why this score?" AI explanation |
| Upload | `/upload` | Drag-and-drop zone; redirects to detail page on success |

### API Endpoints (`/api/v1/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`    | `/health` | Health check |
| `POST`   | `/api/v1/receipts/upload` | Upload receipt → OCR + pHash (parallel) → fraud pipeline → store |
| `POST`   | `/api/v1/receipts/{id}/analyze` | Re-run fraud pipeline on existing receipt (clears old flags and cached explanation) |
| `POST`   | `/api/v1/receipts/{id}/explain` | Generate AI explanation via Gemini 2.5 Flash; cached after first call. See `GEMINI_EXPLAINER.md`. |
| `PATCH`  | `/api/v1/receipts/{id}/review` | Approve or reject a receipt; returns `original_receipt_id` for duplicates |
| `GET`    | `/api/v1/receipts/{id}` | Fetch receipt with fraud flags and OCR data |
| `GET`    | `/api/v1/receipts` | List receipts (paginated, filterable by status) |
| `GET`    | `/api/v1/auth/me` | Current user + business memberships |
| `POST`   | `/api/v1/tenants` | Create a business |
| `GET`    | `/api/v1/tenants` | List your businesses |
| `GET`    | `/api/v1/tenants/{id}/members` | List members of a business |
| `POST`   | `/api/v1/tenants/{id}/members` | Add a member by email |
| `DELETE` | `/api/v1/tenants/{id}/members/{user_id}` | Remove a member |

Every `/api/v1/receipts/*` route requires a Supabase JWT **and** an `X-Tenant-ID`
header; `POST` / `GET /api/v1/tenants` require only the JWT.

### Auth & multi-tenancy

- Authentication is handled by Supabase Auth (GoTrue). The frontend logs in
  against Supabase and sends the resulting JWT as `Authorization: Bearer <token>`
  on every request.
- The backend accepts both HS256 tokens (shared JWT secret) and ES256 tokens
  (verified against the project's JWKS at `/auth/v1/.well-known/jwks.json`,
  cached in memory). Supabase projects using asymmetric signing keys work
  without extra configuration.
- Data requests also carry `X-Tenant-ID: <id>` to select the active business.
  FastAPI verifies the JWT, checks the caller's membership in that business, and
  scopes every query by that tenant. `POST /api/v1/tenants` and
  `GET /api/v1/tenants` are the exceptions — they need only the JWT.
- Create a business with `POST /api/v1/tenants` (you become its owner). Add
  teammates with `POST /api/v1/tenants/{id}/members` — they must already have a
  Supabase account.
- Receipt images are stored in a Supabase Storage bucket named `receipts`, keyed
  by `{tenant_id}/{uuid}.{ext}`.
- Row-Level Security is not yet enabled; tenant isolation is enforced in the
  application layer. Adding RLS as defense-in-depth is a Phase 2 task.

Interactive docs: `http://localhost:8000/docs`

### Tests
53 unit tests covering fraud scorer, all policy rule types, and hash computation — run with `pytest`.

## Local Setup

**Prerequisites:** Python 3.11+, PostgreSQL 15, Tesseract OCR, Node.js 18+

### Backend

```bash
# 1. Create database
createdb fraud_detection

# 2. Install dependencies
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your database credentials

# 4. Run migrations
alembic upgrade head

# 5. Businesses created via POST /api/v1/tenants are auto-seeded with the 6
#    default policy rules. Only backfill an older business manually:
# python seed_policies.py --tenant-id <id>

# 6. Start server
uvicorn app.main:app --reload
```

### Environment

Copy `backend/.env.example` to `backend/.env` and fill in, from your Supabase
project dashboard:

- `DATABASE_URL` — the **pooled** connection string (port 6543), for the app
- `DATABASE_URL_DIRECT` — the **direct** connection string (port 5432), for Alembic
- `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET` (default `receipts` — create this bucket, not public)

Run `cd backend && alembic upgrade head` to apply migrations.

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```

## What's Next

### MVP Polish (before CPSC 490 submission)
- [ ] Deploy backend to Render, frontend to Vercel
- [ ] End-to-end test with 20 sample receipts
- [ ] Accuracy metrics on test dataset

### Phase 2 (CPSC 491)
- Gemini Vision fallback for low-confidence OCR
- Isolation Forest anomaly detection
- Analytics dashboard (employee risk clustering, fraud trends)
- User authentication and review workflow UI

## Project Docs

- [`PROJECT.md`](PROJECT.md) — goals, tech stack, success metrics
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — architecture and technical decisions
- [`FRONTEND.md`](FRONTEND.md) — frontend design direction and page specs
- [`GEMINI_EXPLAINER.md`](GEMINI_EXPLAINER.md) — spec for the "Why this score?" AI explanation feature
- [`API.md`](API.md) — full API reference
- [`TODO.md`](TODO.md) — full task breakdown by semester
