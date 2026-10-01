# Receiptly — AI-Powered Expense Fraud Detection

Senior capstone project, California State University, Fullerton (CPSC 490 → CPSC 491, advisor Dr. Kanika Sood). Receiptly analyzes employee expense receipts to identify suspicious reimbursement claims before approval, using OCR, perceptual hashing, configurable policy rules, and explainable risk scoring.

**Team:** Jim Alvarez, Evan Miller, Syon Chau

**Status:** Spring 2026 MVP complete. In September 2026 the backend moved to Supabase (Postgres, Auth, Storage) with multi-business tenancy. Fall 2026 (CPSC 491) continues toward a deployable product — see [Fall 2026 Roadmap](#fall-2026-roadmap-cpsc-491).

Receiptly is decision support, not automation: it surfaces evidence and a risk score for a human reviewer. It does not approve reimbursements on its own.

## Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI, Python 3.11+, async |
| Database | Supabase Postgres (pooled connection for the app, direct for migrations), SQLAlchemy async, Alembic |
| Auth | Supabase Auth — HS256 and ES256/JWKS JWTs, verified in FastAPI |
| File storage | Supabase Storage, private `receipts` bucket keyed by `{tenant_id}/{uuid}.{ext}` |
| Receipt extraction | Tesseract OCR + preprocessing (replacement/enhanced engine under evaluation in Fall 2026) |
| Duplicate detection | `imagehash` perceptual hashing (pHash) |
| AI explanation | Gemini 2.5 Flash |
| Anomaly detection | scikit-learn + historical pattern features (Fall 2026) |
| Web frontend | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, React Router |
| Mobile | Mobile capture/upload client (Fall 2026) |
| Testing | pytest (local Postgres test database) + manual end-to-end |
| Deployment | Render / Vercel or equivalent for the app (Fall 2026); Supabase already hosts data, auth, and files |

## What's Built

The Spring 2026 MVP pipeline, plus Supabase multi-tenant auth added in September 2026.

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
- Adding a new detector requires one new call here

### Frontend — Reviewer UI

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

### Auth & multi-tenancy (September 2026)

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
112 tests covering the fraud scorer, every policy rule type, hash computation, JWT verification (HS256 and ES256), and tenant isolation — run with `python -m pytest` from `backend/`.

The DB-backed tests run against a **local** Postgres database named `fraud_detection_test` (`createdb fraud_detection_test`; override with `TEST_DATABASE_URL`). The suite refuses to run against any database whose name lacks `test`, so it cannot touch the Supabase database.

## Local Setup

**Prerequisites:** Python 3.11–3.13, Tesseract OCR, Node.js 18+, access to the team's Supabase project, and a local PostgreSQL 15 for running tests

### Backend

```bash
# 1. Create the local *test* database (app data lives in Supabase)
createdb fraud_detection_test

# 2. Install dependencies
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Fill in the Supabase values — see Environment below

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
cp .env.example .env
# Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY (Supabase dashboard → API);
# VITE_API_URL defaults to http://localhost:8000. The app will not load without them.
npm run dev
# Opens at http://localhost:5173
```

## Fall 2026 Roadmap (CPSC 491)

Full detail in [`CPSC_491_Project_Proposal - Evan Miller, Syon Chau, Jim Alvarez.pdf`](CPSC_491_Project_Proposal%20-%20Evan%20Miller,%20Syon%20Chau,%20Jim%20Alvarez.pdf). The semester extends the MVP rather than rebuilding it, in four phases with weekly checkpoints.

### September — Foundation and Core Infrastructure
- [ ] Audit the current extraction pipeline and benchmark candidate replacement libraries/engines against a fixed baseline receipt set
- [ ] Integrate the chosen extraction path; improve preprocessing and low-confidence handling
- [ ] Expand the database schema for scalability, company ownership, and mobile-facing needs
- [ ] Establish baseline extraction metrics so accuracy changes are measurable

### October — Feature Enhancements and Dashboard
- [x] User authentication — Supabase Auth with HS256 and ES256/JWKS verification *(landed early, September)*
- [x] Business-level data separation — every receipt query is tenant-scoped, and each new business is seeded with default policies *(landed early, September)*
- [ ] Company-specific customization of policies, limits, categories, and reviewer settings (per-business rules exist, but there's no API or UI to edit them yet)
- [ ] Historical pattern/anomaly detection wired into the fraud-scoring pipeline
- [ ] Threshold tuning; dashboard statistics, risk summaries, trends, and graphs

### November — Additions and Development
- [ ] Mobile receipt capture/upload client connected to the existing backend endpoints
- [ ] CSV and/or PDF export for review reports
- [ ] Additional model training/tuning and evaluation on diverse receipt samples
- [ ] Production configuration, services, migrations, and release checklist

### December — Deployment and Monitoring
- [ ] Deploy and validate end-to-end (web + mobile upload, analysis, review, export)
- [ ] Monitoring for false positive rate, processing time, database errors, and AI API costs
- [ ] Final evaluation, documentation, and handoff materials

### Evaluation Metrics
Precision, recall, F1, false positive rate, extraction/OCR accuracy, and per-receipt processing time, measured against the baseline established in September.

### Ownership

| Member | Primary area |
|---|---|
| Jim Alvarez | Receipt intelligence and extraction accuracy; CSV/PDF report export; dashboard (shared with Evan) |
| Evan Miller | Analytics; advanced fraud and anomaly detection; dashboard (shared with Jim) |
| Syon Chau | Backend/frontend integration; multi-business operations; user authentication; mobile scanning/upload |

Integration, testing, deployment, and final documentation are collaborative.

### Known Risks
- Extraction stays inconsistent on blurry, low-contrast, or unusual layouts → benchmark multiple approaches, improve preprocessing, add low-confidence fallback, track accuracy
- Anomaly detection produces too many false positives → tune on diverse receipts, track FPR, keep signals explainable, preserve human review
- Mobile integration overruns → ship capture/upload and backend connectivity first, defer UI polish
- Cloud/AI costs exceed free tiers → cache AI results, monitor usage, rate-limit expensive calls, keep local processing where practical
- Deployment config/database failures → migrations, env-based config, staged checks, backups, health endpoints

## Project Docs

- [`PROJECT.md`](PROJECT.md) — goals, tech stack, success metrics
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — architecture and technical decisions
- [`FRONTEND.md`](FRONTEND.md) — frontend design direction and page specs
- [`GEMINI_EXPLAINER.md`](GEMINI_EXPLAINER.md) — spec for the "Why this score?" AI explanation feature
- [`API.md`](API.md) — full API reference
- [`TODO.md`](TODO.md) — full task breakdown by semester
- [`CPSC490_Final_Report.pdf`](CPSC490_Final_Report.pdf) — Spring 2026 final report
