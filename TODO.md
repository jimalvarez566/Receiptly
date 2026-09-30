# TODO

## Up Next (MVP Polish)

### Deployment
- [ ] Deploy backend to Render
- [ ] Deploy frontend to Vercel
- [ ] Test end-to-end with 20 sample receipts

## Semester 1 Completion (CPSC 490)
- [x] Document API endpoints in API.md
- [ ] Create test dataset (100 legitimate, 50 fraudulent receipts)
- [ ] Measure and document accuracy metrics
- [ ] Write final report for CPSC 490
- [ ] Prepare demo for presentation
- [ ] Tag release as v1.0-mvp

## Semester 2 - Phase 2 Features (CPSC 491)

### Month 1: Advanced Detection
- [ ] Integrate Gemini Vision API for image analysis (OCR fallback when Tesseract confidence < 70)
- [ ] Build AI analyzer service (Claude API integration for fraud explanations + categorization)
- [ ] Implement Isolation Forest anomaly detector
- [ ] Add expense categorization validation
- [ ] Enhance risk scoring with weighted signals
- [ ] Test improved accuracy vs MVP

### Month 2: Analytics Dashboard
- [ ] Create employee statistics aggregation
- [ ] Implement K-means clustering for risk profiles
- [ ] Build time series analysis for fraud trends
- [ ] Create analytics API endpoints
- [ ] Build React analytics dashboard components
- [ ] Add data visualization (charts, heatmaps)

### Month 3: Production Features
- [ ] Add user authentication (JWT)
- [ ] Build fraud review workflow UI
- [ ] Implement export functionality (CSV, PDF)
- [ ] Add API rate limiting
- [ ] Optimize database queries with indexes
- [ ] Set up monitoring and logging

### Month 4: Final Polish
- [ ] Comprehensive testing (unit, integration, e2e)
- [ ] Performance optimization
- [ ] Security audit
- [ ] Final documentation
- [ ] Create demo video
- [ ] Write CPSC 491 final report
- [ ] Tag release as v2.0-production

## Ideas for Future (Post-Graduation)
- [ ] Mobile app (React Native)
- [ ] Integration with Expensify/Concur APIs
- [ ] Multi-tenant support (company accounts)
- [ ] Real-time fraud alerts (email/Slack)
- [ ] Custom ML model training on company data
- [ ] Notion/Linear export integration

## Next (Phase 2 hardening of multi-tenancy)
- [ ] Add Row-Level Security policies on tenant tables (defense-in-depth)
- [ ] Enforce roles (admin vs member) on tenant + policy_rule endpoints
- [ ] Invitation tokens + emails for users without a Supabase account yet
- [ ] Serve receipt images to the frontend via short-lived signed URLs
- [ ] Handle GoTrue admin-list pagination in lookup_user_id_by_email

## Done ✅

### Project Setup
- [x] Created PROJECT.md, IMPLEMENTATION.md, TODO.md
- [x] Defined database schema and chose tech stack
- [x] Set up FastAPI project structure
- [x] Created database models (Employee, Receipt, FraudFlag, PolicyRule)
- [x] Set up PostgreSQL locally with `fraud_detection` database
- [x] Created initial Alembic migration and applied schema

### OCR Service (`app/services/ocr.py`)
- [x] Image preprocessing pipeline (grayscale, upscale, autocontrast, sharpen, binarize)
- [x] Merchant name detection using Tesseract line-height hierarchy + boilerplate filtering
- [x] Transaction date and time extraction (regex, multiple formats)
- [x] Total amount extraction (keyword-anchored, fallback to largest dollar figure)
- [x] Line item parsing with footer zone cutoff
- [x] Integrated OCR into upload endpoint (runs automatically on every upload)
- [x] OCR and pHash now run in parallel via `asyncio.gather` (saves ~1–2s per upload)

### Fraud Detection Backend
- [x] Duplicate detection (`app/services/duplicate_detector.py`)
  - [x] Perceptual hashing (pHash via `imagehash`) stored as `image_hash` on Receipt
  - [x] Hamming distance comparison against all existing hashes (threshold ≤ 10/64)
  - [x] Generates `FraudFlag` with `flag_type="duplicate"`, `severity="high"`, `confidence=100%`
- [x] Policy validator (`app/services/policy_validator.py`) — 5 rule types:
  - [x] `amount_limit` — flags receipts exceeding a per-category spending cap
  - [x] `future_date` — flags receipts with transaction dates in the future
  - [x] `vendor_category` — flags merchants not matching approved expense categories (keyword-based)
  - [x] `round_number` — flags suspiciously round totals (e.g. $50.00, $25.50) above a threshold
  - [x] `short_window_duplicate` — flags same employee submitting similar receipts within 48h
- [x] Policy seed script (`backend/seed_policies.py`) — idempotent, seeds 6 default rules
- [x] Fraud scorer (`app/services/fraud_scorer.py`)
  - [x] Weighted score 0–100 combining flag type, severity, and confidence
  - [x] Diminishing returns for multiple flags (each successive flag contributes half)
  - [x] Risk levels: low (0–29), medium (30–69), high (70–100)
- [x] Pipeline orchestrator (`app/services/fraud_detection_pipeline.py`)
  - [x] Single `run_fraud_pipeline(receipt, db)` function used by both upload and analyze
  - [x] Extensible: adding Phase 2 detectors requires one new call here

### API Endpoints
- [x] `POST /api/v1/receipts/upload` — upload, OCR, hash, pipeline, store
- [x] `GET  /api/v1/receipts/{id}` — fetch receipt with fraud flags
- [x] `GET  /api/v1/receipts` — paginated list, filterable by status
- [x] `POST /api/v1/receipts/{id}/analyze` — re-run fraud pipeline, clears old flags first
- [x] `PATCH /api/v1/receipts/{id}/review` — approve/reject; surfaces `original_receipt_id` for duplicates

### Tests
- [x] pytest configured (`pytest.ini`, `asyncio_mode=auto`)
- [x] `tests/test_services/test_fraud_scorer.py` — 17 tests, full scorer coverage
- [x] `tests/test_services/test_policy_validator.py` — 30 tests, all 4 sync rule types
- [x] `tests/test_services/test_duplicate_detector.py` — 6 tests, hash computation
- [x] 53 tests, all passing

### Frontend (`frontend/`)
- [x] Vite + React 18 + TypeScript + Tailwind CSS v4 + shadcn/ui initialized
- [x] Dark fintech theme (#0a0a0a background, monospace numbers, risk color coding)
- [x] Sidebar layout with React Router — Dashboard / Receipts / Upload
- [x] ErrorBoundary wrapping all pages
- [x] Typed API client (`src/lib/api.ts`) covering all 5 backend endpoints
- [x] Dashboard page — volume cards + risk breakdown, all clickable
- [x] Receipts list page — table, status filter, pagination, live data
- [x] Receipt detail page — fraud score, OCR fields, line items, fraud flags, approve/reject/re-analyze
- [x] Upload page — drag-and-drop zone, loading state, redirect on success

### Supabase Auth & Multi-Tenancy (`feature/supabase-multitenant-auth`)
- [x] `Tenant` + `Membership` models; `tenant_id` on receipts / policy_rules / employees
- [x] Alembic migration `a1b2c3d4e5f6` (chained after `6d34720ba4d7`)
- [x] Supabase JWT verification (`get_current_user`) + active-tenant resolver (`get_current_context`)
- [x] Supabase REST client — admin email lookup + Storage upload/delete
- [x] Tenants + members API; `GET /api/v1/auth/me`
- [x] Receipts endpoints scoped by tenant; images stored in Supabase Storage
- [x] All `/api/v1/receipts/*` endpoints (upload, analyze, explain, review, get, list) require JWT + `X-Tenant-ID`
- [x] Fraud pipeline scoped by tenant (policy rules, pHash + short-window duplicate scans, review duplicate-chain)
- [x] Default policy rules auto-seeded on business creation; `seed_policies.py --tenant-id` for backfill
- [x] Backend verifies both HS256 and ES256 (JWKS) Supabase tokens — real user sessions work end to end
- [x] Frontend: Supabase signup/login, active-business selection + sidebar switcher, auth headers on all API calls
