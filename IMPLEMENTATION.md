# Implementation Details

## System Architecture

### High-Level Flow
```
Receipt Upload → OCR Extraction → Fraud Detection Pipeline → Risk Scoring → Response
                                    ├─ Duplicate Check
                                    ├─ Policy Validation
                                    ├─ Anomaly Detection (Phase 2)
                                    └─ AI Analysis (Phase 2)
```

### Backend Architecture

#### Directory Structure
```
backend/
├── app/
│   ├── main.py                 # FastAPI app, CORS, router registration
│   ├── config.py              # Pydantic settings from .env
│   ├── database.py            # Async SQLAlchemy engine, session, Base
│   │
│   ├── models/                # SQLAlchemy ORM models (built ✅)
│   │   ├── receipt.py         # Receipt with OCR fields, fraud score, status
│   │   ├── fraud_flag.py      # Individual fraud signals per receipt
│   │   ├── employee.py        # Employee spending history
│   │   └── policy_rule.py     # Configurable fraud rules
│   │
│   ├── schemas/               # Pydantic request/response schemas (built ✅)
│   │   ├── receipt.py         # ReceiptResponse, ReceiptListResponse
│   │   └── fraud.py           # FraudFlagResponse
│   │
│   ├── api/                   # API routes
│   │   ├── v1/
│   │   │   └── receipts.py   # Upload, get by ID, list (built ✅)
│   │   └── deps.py           # DB session dependency
│   │
│   ├── services/              # Business logic
│   │   ├── ocr/             # PaddleOCR pipeline (built ✅)
│   │   ├── duplicate_detector.py    # Perceptual hashing (planned)
│   │   ├── policy_validator.py      # Rule-based checks (planned)
│   │   ├── anomaly_detector.py      # Isolation Forest (Phase 2)
│   │   ├── ai_analyzer.py           # Claude/Gemini API calls (Phase 2)
│   │   └── fraud_scorer.py          # Combine signals into risk score (planned)
│   │
│   └── repositories/          # Data access layer (planned)
│       ├── receipt_repo.py
│       └── fraud_flag_repo.py
│
├── tests/                     # (planned)
├── requirements.txt
├── .env / .env.example
└── alembic/                   # Migrations (initial schema applied ✅)
```
### Frontend Architecture
See `FRONTEND.md` for full frontend implementation details, design decisions, and page specs.

Frontend stack: React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, React Router.
Connects to backend via `VITE_API_URL` environment variable.

Directory structure:
frontend/
├── src/
│   ├── pages/         # Dashboard, ReceiptsList, ReceiptDetail, Upload
│   ├── components/    # Shared UI components
│   └── main.tsx
├── .env
└── vite.config.ts

## Database Schema

### Core Tables
```sql
-- Employees (for tracking spending patterns)
CREATE TABLE employees (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    department VARCHAR(100),
    role VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Receipts
CREATE TABLE receipts (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER REFERENCES employees(id),
    
    -- File storage
    image_path TEXT NOT NULL,
    image_hash VARCHAR(64),  -- pHash for duplicate detection
    
    -- Extracted data (from OCR)
    merchant VARCHAR(255),
    amount DECIMAL(10, 2),
    transaction_date DATE,
    category VARCHAR(100),
    items JSONB,  -- Itemized purchases
    
    -- Metadata
    ocr_confidence DECIMAL(5, 2),  -- 0-100
    ocr_method VARCHAR(20),  -- 'paddle' or 'gemini'
    
    -- Analysis results
    fraud_score INTEGER,  -- 0-100
    risk_level VARCHAR(20),  -- 'low', 'medium', 'high'
    status VARCHAR(50) DEFAULT 'pending',  -- 'pending', 'approved', 'rejected', 'flagged'
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    analyzed_at TIMESTAMP,
    reviewed_at TIMESTAMP
);

-- Fraud Flags (many-to-one with receipts)
CREATE TABLE fraud_flags (
    id SERIAL PRIMARY KEY,
    receipt_id INTEGER REFERENCES receipts(id) ON DELETE CASCADE,
    
    flag_type VARCHAR(50) NOT NULL,  -- 'duplicate', 'policy_violation', 'anomaly', 'ai_suspicious'
    severity VARCHAR(20) NOT NULL,    -- 'low', 'medium', 'high'
    
    description TEXT,  -- Human-readable explanation
    details JSONB,     -- Structured data (e.g., which policy violated, duplicate receipt ID)
    
    confidence_score DECIMAL(5, 2),  -- How confident we are in this flag
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- Policy Rules (configurable)
CREATE TABLE policy_rules (
    id SERIAL PRIMARY KEY,
    rule_name VARCHAR(100) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,  -- 'amount_limit', 'approved_vendors', 'category_restriction'
    
    parameters JSONB NOT NULL,  -- e.g., {"category": "meals", "limit": 75.00}
    
    is_active BOOLEAN DEFAULT TRUE,
    severity VARCHAR(20) DEFAULT 'medium',
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_receipts_employee ON receipts(employee_id);
CREATE INDEX idx_receipts_image_hash ON receipts(image_hash);
CREATE INDEX idx_receipts_status ON receipts(status);
CREATE INDEX idx_fraud_flags_receipt ON fraud_flags(receipt_id);
CREATE INDEX idx_fraud_flags_type ON fraud_flags(flag_type);
```

### Phase 2 Extensions
```sql
-- Employee spending statistics (for anomaly detection)
CREATE TABLE employee_stats (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id),
    
    -- Aggregated metrics
    avg_expense_amount DECIMAL(10, 2),
    std_dev_amount DECIMAL(10, 2),
    total_expenses INTEGER,
    fraud_count INTEGER DEFAULT 0,
    
    -- Category breakdown
    category_stats JSONB,  -- {"meals": {"avg": 45, "count": 20}, ...}
    
    -- Risk clustering
    risk_cluster INTEGER,  -- From K-means (0=low, 1=medium, 2=high)
    
    last_updated TIMESTAMP DEFAULT NOW()
);

-- Fraud analysis cache (to avoid re-processing)
CREATE TABLE analysis_cache (
    id SERIAL PRIMARY KEY,
    
    image_hash VARCHAR(64) UNIQUE NOT NULL,
    analysis_result JSONB NOT NULL,  -- Full analysis output
    
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP  -- For cache invalidation
);

CREATE INDEX idx_cache_hash ON analysis_cache(image_hash);
CREATE INDEX idx_cache_expires ON analysis_cache(expires_at);
```

## API Endpoints

### Phase 1 (MVP)
```
POST   /api/v1/receipts/upload
POST   /api/v1/receipts/analyze
GET    /api/v1/receipts/{id}
GET    /api/v1/receipts
PATCH  /api/v1/receipts/{id}/review  # Approve/reject
```

### Phase 2 (Production)
```
GET    /api/v1/analytics/overview
GET    /api/v1/analytics/trends
GET    /api/v1/analytics/employee-risks
GET    /api/v1/analytics/fraud-patterns

POST   /api/v1/policies
GET    /api/v1/policies
PATCH  /api/v1/policies/{id}
```

## Fraud Detection Pipeline

### Service Layer Design
```python
# services/fraud_detection_pipeline.py

class FraudDetectionPipeline:
    """
    Orchestrates all fraud detection methods.
    Easy to add new detectors without changing existing code.
    """
    
    def __init__(self):
        self.detectors = [
            DuplicateDetector(),
            PolicyValidator(),
            # Phase 2: add these
            # AnomalyDetector(),
            # AIAnalyzer(),
        ]
        self.scorer = FraudScorer()
    
    async def analyze(self, receipt_data: dict) -> FraudAnalysisResult:
        """Run all detectors and combine results."""
        flags = []
        
        for detector in self.detectors:
            detector_flags = await detector.detect(receipt_data)
            flags.extend(detector_flags)
        
        risk_score = self.scorer.calculate_score(flags)
        
        return FraudAnalysisResult(
            flags=flags,
            risk_score=risk_score,
            risk_level=self._get_risk_level(risk_score)
        )
```

### Individual Detector Interfaces
```python
# services/base_detector.py

from abc import ABC, abstractmethod

class FraudDetector(ABC):
    """Base class for all fraud detectors."""
    
    @abstractmethod
    async def detect(self, receipt_data: dict) -> List[FraudFlag]:
        """
        Analyze receipt and return list of fraud flags.
        Return empty list if no fraud detected.
        """
        pass
```

Each detector implements this interface:
- `DuplicateDetector`: Uses perceptual hashing
- `PolicyValidator`: Checks configurable rules
- `AnomalyDetector` (Phase 2): Uses Isolation Forest
- `AIAnalyzer` (Phase 2): Calls Claude/Gemini APIs

## OCR Service Design (`app/services/ocr/`)

PaddleOCR (PP-OCRv6, `small` models by default via `OCR_PADDLE_MODEL`) replaced the original Tesseract engine after both were benchmarked with the eval harness in `backend/eval/`.

### Line Reconstruction (`paddle.py`)
Paddle returns independent text boxes, not lines — a quantity, item name and price come back as three detections. Lines are rebuilt from box geometry before any parsing:
- **Score filter**: drops recognitions scoring below 0.5 (mostly separator rows and smudges)
- **De-tilt**: box centres are rotated by the page's median baseline angle, so a slanted phone photo still pairs a description with its price
- **Row grouping**: boxes whose centres agree to within half a glyph height form one row, read left to right

### Merchant Name Detection
- **Line height**: the tallest line wins; height is measured along each box's own edges (a tilted box's axis-aligned height is inflated), and a row's height is that of its longest box so a short padded "$15.95" can't outrank the store name
- **Boilerplate filter**: strips URLs, "thank you", payment keywords, and long numeric sequences before ranking
- **Zone filter**: ignores lines below 60% of the receipt's text extent (footer territory), measured from the text rather than the image edges since photos include background
- **Token cleanup**: removes pure-symbol tokens and 1–2 char lowercase fragments from the winning line

### Field Extraction (from raw text)
| Field | Strategy |
|---|---|
| `merchant` | Line-height detection (above) |
| `transaction_date` | Regex covering MM/DD/YYYY, YYYY-MM-DD, month-name formats |
| `transaction_time` | Regex for HH:MM with optional seconds and AM/PM |
| `amount` | Keyword-anchored search (`total`, `amount due`); falls back to largest dollar figure |
| `items` | Line-by-line regex; **stops at first footer keyword** (subtotal/tax/total) to exclude payment and card lines; handles trailing taxability indicators (X, N) |

### Confidence Score
Simple 0–100 score: 25 points per successfully extracted field (merchant, date, total, at least one item).

### Stored Output
The `items` JSONB column stores:
```json
{
  "line_items": [{"description": "BOYS CREW", "amount": 9.48}],
  "transaction_time": "22:36:22",
  "raw_text": "..."
}
```

## AI Integration Strategy (Phase 2)

### OCR Fallback (Gemini Vision)
If OCR confidence < 70, fall back to Gemini Vision for extraction. This keeps costs near zero for clear receipts while recovering accuracy on damaged or handwritten ones.

### Claude API Usage (Phase 2)

**Use Cases:**
1. Expense categorization (is this really a business meal?)
2. Itemized purchase analysis (detect personal items)
3. Fraud explanation generation (natural language)

**Cost Control:**
- Only call for receipts flagged by other detectors
- Cache results by image hash
- Batch multiple questions in one API call

### Gemini Vision Usage (Phase 2)

**Use Cases:**
1. OCR fallback (when PaddleOCR fails)
2. Image authenticity analysis (detect Photoshop)
3. Receipt quality assessment

**Cost Control:**
- Free tier: 60 requests/min, 1500/day
- Only use for suspicious receipts
- Cache results

## Configuration

### Environment Variables
```bash
# .env.example

# Database
DATABASE_URL=postgresql://user:pass@localhost/fraud_detection

# AI APIs
CLAUDE_API_KEY=sk-ant-...
GEMINI_API_KEY=...

# Application
DEBUG=False
SECRET_KEY=your-secret-key
CORS_ORIGINS=http://localhost:3000,https://yourapp.com

# File Storage
UPLOAD_DIR=./uploads
MAX_FILE_SIZE_MB=10

# Feature Flags (for gradual rollout)
ENABLE_GEMINI_VISION=False
ENABLE_ANOMALY_DETECTION=False
ENABLE_AI_ANALYSIS=False
```

### Policy Configuration
```json
// Example policy rules stored in database
{
  "meal_limit": {
    "category": "meals",
    "max_amount": 75.00,
    "severity": "medium"
  },
  "future_date": {
    "type": "date_validation",
    "rule": "no_future_dates",
    "severity": "high"
  },
  "approved_vendors": {
    "type": "vendor_whitelist",
    "vendors": ["Staples", "Office Depot", "Amazon Business"],
    "severity": "low"
  }
}
```

## Testing Strategy

### Unit Tests
- Each fraud detector in isolation
- Mock external APIs (Claude, Gemini)
- Test edge cases (malformed receipts, missing data)

### Integration Tests
- Full fraud detection pipeline
- API endpoints with real database
- OCR accuracy on test receipt dataset

### Test Data
- 100 synthetic receipts (legitimate)
- 50 synthetic receipts (fraudulent)
- 20 real receipts (from personal collection)

## Deployment

### Environment Setup

**Development:**
- Local PostgreSQL database
- SQLite for quick testing
- All AI features enabled

**Production:**
- Supabase/Render Postgres
- Environment-based feature flags
- Gradual rollout of expensive features

### CI/CD (Future)
- GitHub Actions for tests
- Auto-deploy to Render on main branch push
- Database migrations run automatically

## Performance Targets

- Receipt upload: <2 seconds
- OCR extraction: <5 seconds (PaddleOCR) or <10 seconds (Gemini)
- Duplicate detection: <1 second
- Full fraud analysis: <15 seconds total
- API response time (excluding analysis): <200ms

## Monitoring (Future)

### Metrics to Track
- Fraud detection accuracy (precision/recall)
- False positive rate
- Processing time per stage
- API costs (Claude, Gemini)
- Database query performance

### Alerting
- High false positive rate (>20%)
- API costs exceeding budget
- Processing time >30 seconds
- Database errors