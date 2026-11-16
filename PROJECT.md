# AI-Powered Expense Fraud Detection System

## Overview
An intelligent fraud detection system that analyzes employee expense reports using computer vision, natural language processing, and machine learning to identify fraudulent submissions before reimbursement. The system combines multiple fraud detection techniques to provide explainable risk scores with specific fraud indicators.

## Project Timeline
- **Spring 2026 (CPSC 490)**: MVP with core fraud detection functionality
- **Fall 2026 (CPSC 491)**: Production features including analytics dashboard and advanced ML

## Tech Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL (development: local, production: Supabase/Render)
- **ORM**: SQLAlchemy with async support
- **API Documentation**: Auto-generated OpenAPI/Swagger

### AI/ML
- **Claude API**: Text analysis, categorization, fraud explanations (Sonnet 4)
- **Gemini Vision API**: Image analysis, OCR fallback for difficult receipts
- **PaddleOCR**: Primary text extraction (open-source, local)
- **scikit-learn**: Isolation Forest for anomaly detection, K-means for clustering
- **ImageHash**: Perceptual hashing for duplicate detection (pHash algorithm)

### Frontend
- **Framework**: React 18 with TypeScript
- **Styling**: Tailwind CSS
- **Components**: shadcn/ui
- **Charts**: Recharts
- **State Management**: React hooks + Context API

### Deployment
- **Backend**: Render.com (free tier)
- **Frontend**: Vercel (free tier)
- **Database**: Supabase or Render Postgres (free tier)
- **File Storage**: Local filesystem (dev), Cloudflare R2 (production)

## Core Features

### Phase 1 (Spring 2026 - MVP)
1. **Receipt Processing** ✅
   - File upload (PNG, JPG, PDF)
   - OCR text extraction via Tesseract with image preprocessing pipeline
   - Structured data parsing (merchant, amount, date, items) with footer zone cutoff
   - OCR and pHash computed in parallel via `asyncio.gather`

2. **Fraud Detection** ✅
   - Duplicate detection via perceptual hashing (pHash, Hamming distance ≤ 10)
   - Policy validation — 5 configurable rule types (amount_limit, future_date, vendor_category, round_number, short_window_duplicate)
   - Risk scoring 0–100 with diminishing returns for multiple flags
   - Pipeline orchestrator (`fraud_detection_pipeline.py`) used by upload + analyze endpoints
   - Review endpoint with `original_receipt_id` context for duplicate chains
   - 53 unit tests, all passing

3. **Simple UI** ✅
   - Upload page — drag-and-drop, loading state, redirect on success
   - Receipts list — paginated table with status filter
   - Receipt detail — fraud score, OCR fields, fraud flags, approve/reject/re-analyze
   - Dashboard — volume and risk breakdown summary cards

### Phase 2 (Fall 2026 - Production)
1. **Advanced Detection**
   - Image authenticity analysis (Gemini Vision)
   - Behavioral anomaly detection (Isolation Forest)
   - Intelligent expense categorization (Claude API)
   - Enhanced risk scoring with weighted signals

2. **Analytics Dashboard**
   - Employee risk clustering (K-means)
   - Fraud trend analysis (time series)
   - Department/category breakdowns
   - Predictive risk scoring

3. **Production Features**
   - User authentication
   - Fraud review workflow (approve/reject)
   - Export functionality (CSV, PDF)
   - API rate limiting and caching

## Architecture Principles

### API Design
- RESTful endpoints with clear naming
- Async handlers for I/O operations
- Proper error handling with informative messages
- Input validation using Pydantic models
- API versioning prepared (/api/v1/...)

### Database Design
- Normalized schema with proper relationships
- Indexes on frequently queried fields
- JSONB for flexible fraud flag storage
- Audit trail for all fraud decisions

### ML Pipeline
- Modular design: separate services for each detection method
- Easy to add new fraud detection techniques
- Cached results to minimize API costs
- Model versioning and retraining strategy

### Code Organization
- Clear separation of concerns
- Domain-driven structure (not CRUD-based)
- Services layer for business logic
- Repositories for data access
- Type hints throughout

## Success Metrics

### Technical
- Fraud detection accuracy: 85%+ true positive rate
- False positive rate: <15%
- Processing time: <15 seconds per receipt
- OCR accuracy: 95%+ on clear receipts
- API uptime: 99%+

### Business
- Manual audit time reduction: 70%+
- Cost per analysis: <$0.05 per receipt
- User satisfaction: Fraud reviewers find flags actionable

## Cost Management

### API Costs (Target: <$5/month during development)
- **Claude API**: ~$0.01-0.02 per receipt (use only for complex analysis)
- **Gemini Vision API**: Free tier (60 req/min, 1500/day)
- **PaddleOCR**: Free, unlimited (process locally first)

### Optimization Strategies
- Cache all AI analysis results in database
- Use PaddleOCR first, fallback to Gemini Vision only when confidence <70%
- Batch process receipts when possible
- Store perceptual hashes to avoid re-processing

## Development Guidelines

### Code Quality
- Type hints on all functions
- Docstrings for public APIs
- Unit tests for critical fraud detection logic
- Integration tests for API endpoints

### Git Workflow
- Feature branches for new functionality
- Descriptive commit messages
- Tag releases by semester (v1.0-mvp, v2.0-production)

### Documentation
- Keep IMPLEMENTATION.md updated with technical decisions
- Update TODO.md weekly
- API.md for endpoint documentation
- Comments for complex fraud detection logic

## Future Extensibility

### Designed to Support
- Additional fraud detection methods (just add new service)
- Multiple AI providers (abstract AI calls behind interface)
- Different expense management system integrations
- Multi-tenant support (company accounts)
- Mobile app (API-first design)

### Integration Points
- Webhook support for expense platforms
- Export to Notion, Linear, Slack
- SSO authentication
- Third-party analytics tools1