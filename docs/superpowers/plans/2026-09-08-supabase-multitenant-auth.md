# Supabase Multi-Tenant Auth Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the FastAPI expense-fraud backend multi-tenant — multiple businesses, multiple users per business, strict per-business data isolation — with authentication and file storage provided by an existing Supabase project.

**Architecture:** Supabase owns auth (GoTrue), the Postgres database, and file storage. FastAPI stays the only API the frontend talks to for data: it verifies the Supabase-issued JWT, resolves the caller's active business from an `X-Tenant-ID` header against a `memberships` table, and scopes every query by that tenant in Python. No Row-Level Security in this phase. Alembic keeps owning the schema.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, asyncpg, Alembic, Pydantic v2, `python-jose` (JWT verification), `httpx` (Supabase Admin + Storage REST calls), `pytest` + `pytest-asyncio` (tests against a local Postgres test database).

---

## Reference: the spec

`docs/superpowers/specs/2026-09-08-supabase-multitenant-auth-design.md` — read it before starting.

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `backend/pytest.ini` | pytest config: `asyncio_mode = auto`, test path |
| `backend/app/auth.py` | JWT verification, `RequestContext`, `get_current_user` (JWT only) and `get_current_context` (JWT + active tenant) dependencies |
| `backend/app/supabase_client.py` | thin async wrappers over Supabase REST: `lookup_user_id_by_email`, `upload_object`, `delete_object` |
| `backend/app/models/tenant.py` | `Tenant` model |
| `backend/app/models/membership.py` | `Membership` model |
| `backend/app/schemas/tenant.py` | Pydantic request/response models for tenants, members, and `/auth/me` |
| `backend/app/api/v1/tenants.py` | tenants + members router |
| `backend/app/api/v1/auth_routes.py` | `/auth/me` router |
| `backend/alembic/versions/a1b2c3d4e5f6_multi_tenant.py` | migration: new tables + `tenant_id` columns |
| `backend/tests/conftest.py` | env setup, test engine, session/client fixtures, JWT mint helper, seed fixtures |
| `backend/tests/test_auth.py` | JWT dependency tests |
| `backend/tests/test_tenants.py` | tenant + member endpoint tests |
| `backend/tests/test_receipts_scoping.py` | cross-tenant isolation tests for receipts |

### Modified files

| Path | Change |
|---|---|
| `backend/requirements.txt` | add `python-jose[cryptography]`, `httpx`, `pytest`, `pytest-asyncio` |
| `backend/app/config.py` | add Supabase + test-DB settings |
| `backend/.env.example` | document the new env vars |
| `backend/alembic/env.py` | use `DATABASE_URL_DIRECT` for migrations |
| `backend/app/models/__init__.py` | export `Tenant`, `Membership` |
| `backend/app/models/receipt.py` | add `tenant_id`, `submitted_by_user_id`, relationship, index |
| `backend/app/models/policy_rule.py` | add `tenant_id`, index |
| `backend/app/models/employee.py` | add `tenant_id`, swap global `email` unique for `(tenant_id, email)` |
| `backend/app/schemas/receipt.py` | add `tenant_id`, `submitted_by_user_id` to `ReceiptResponse` |
| `backend/app/api/v1/receipts.py` | inject `get_current_context`, scope all queries, store files in Supabase Storage |
| `backend/app/main.py` | include the two new routers |

### Decisions locked in here

- **Errors:** use FastAPI's built-in `HTTPException`. Its default handler already emits a consistent `{"detail": ...}` body, so no custom exception handler is needed. Missing `X-Tenant-ID` is caught by reading the header as optional and raising `HTTPException(400)` ourselves (a required `Header(...)` would give 422).
- **UUIDs:** use SQLAlchemy's dialect-agnostic `Uuid` type for all `auth.users.id` references. No FK to the `auth` schema.
- **Tests** run against a real local Postgres database `fraud_detection_test` (JSONB/Numeric need real Postgres). Tables are created and dropped per test function. Supabase REST calls are monkeypatched — tests never hit the network.
- **`alembic upgrade head`** is validated once manually (Task 11); the automated suite does not run migrations.

---

## Task 1: Test tooling, dependencies, and config settings

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/pytest.ini`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_smoke.py` (temporary, deleted in Step 8)

- [ ] **Step 1: Add dependencies**

Replace the contents of `backend/requirements.txt` with:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.0
pydantic==2.10.3
pydantic-settings==2.7.0
python-multipart==0.0.20
python-dotenv==1.0.1
python-jose[cryptography]==3.3.0
httpx==0.28.1
pytest==8.3.4
pytest-asyncio==0.25.0
```

- [ ] **Step 2: Install**

Run: `cd backend && pip install -r requirements.txt`
Expected: installs `python-jose`, `httpx`, `pytest`, `pytest-asyncio` with no errors.

- [ ] **Step 3: Create the local test database**

Run: `createdb fraud_detection_test`
Expected: no output (success). If it already exists, that is fine.

- [ ] **Step 4: Add pytest config**

Create `backend/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 5: Add settings**

In `backend/app/config.py`, inside the `Settings` class, add these fields immediately after the existing `GEMINI_API_KEY` line:

```python
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "receipts"

    # Database — direct connection (port 5432) is used by Alembic;
    # DATABASE_URL (may be the pooled port 6543) is used by the app runtime.
    DATABASE_URL_DIRECT: str = ""
```

- [ ] **Step 6: Update `.env.example`**

Replace the contents of `backend/.env.example` with:

```
# Database
# App runtime — use the Supabase POOLED connection string (port 6543) in production.
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection
# Alembic — use the Supabase DIRECT connection string (port 5432).
DATABASE_URL_DIRECT=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection

# Application
DEBUG=True
SECRET_KEY=change-me-in-production
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# File Storage (local fallback path, unused once Supabase Storage is configured)
UPLOAD_DIR=./uploads
MAX_FILE_SIZE_MB=10

# Supabase
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_JWT_SECRET=your-project-jwt-secret
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=receipts

# AI APIs (Phase 2)
CLAUDE_API_KEY=
GEMINI_API_KEY=

# Feature Flags
ENABLE_GEMINI_VISION=False
ENABLE_ANOMALY_DETECTION=False
ENABLE_AI_ANALYSIS=False
```

- [ ] **Step 7: Create `conftest.py`**

Create `backend/tests/conftest.py`. The `os.environ.setdefault` block MUST come before any `from app...` import so `Settings()` picks up test values:

```python
import os
import time
import uuid

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SUPABASE_STORAGE_BUCKET", "receipts")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test",
)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.main import app
import app.models  # noqa: F401  — ensure all models are registered on Base.metadata

test_engine = create_async_engine(settings.DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with TestSessionLocal() as session:
        yield session
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db_session):
    async def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def make_token(user_id: str, email: str = "user@example.com", expired: bool = False) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "aud": "authenticated",
        "exp": now - 60 if expired else now + 3600,
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def user_a_id():
    return str(uuid.uuid4())


@pytest.fixture
def user_b_id():
    return str(uuid.uuid4())
```

- [ ] **Step 8: Prove the harness runs**

Create `backend/tests/test_smoke.py`:

```python
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

Run: `cd backend && pytest tests/test_smoke.py -v`
Expected: PASS (1 passed). Then delete the file: `rm tests/test_smoke.py`

- [ ] **Step 9: Commit**

```bash
cd backend && git add requirements.txt pytest.ini app/config.py .env.example tests/conftest.py
git commit -m "chore: add test harness, Supabase settings, and dependencies"
```

---

## Task 2: JWT verification dependency (`get_current_user`)

**Files:**
- Create: `backend/app/auth.py`
- Test: `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_auth.py`:

```python
import uuid

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import CurrentUser, get_current_user
from tests.conftest import make_token


@pytest.fixture
def probe_app():
    probe = FastAPI()

    @probe.get("/whoami")
    async def whoami(user: CurrentUser = Depends(get_current_user)):
        return {"user_id": str(user.user_id), "email": user.email}

    return probe


@pytest.fixture
async def probe_client(probe_app):
    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_valid_token_returns_user(probe_client):
    uid = str(uuid.uuid4())
    token = make_token(uid, "alice@example.com")
    resp = await probe_client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": uid, "email": "alice@example.com"}


async def test_missing_token_is_401(probe_client):
    resp = await probe_client.get("/whoami")
    assert resp.status_code == 401


async def test_malformed_token_is_401(probe_client):
    resp = await probe_client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_expired_token_is_401(probe_client):
    token = make_token(str(uuid.uuid4()), expired=True)
    resp = await probe_client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'` (collection error).

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/auth.py`:

```python
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: UUID
    email: str | None


def _decode(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    payload = _decode(creds.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    return CurrentUser(user_id=UUID(sub), email=payload.get("email"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/auth.py tests/test_auth.py
git commit -m "feat: add Supabase JWT verification dependency"
```

---

## Task 3: `Tenant` and `Membership` models + existing-model changes

**Files:**
- Create: `backend/app/models/tenant.py`
- Create: `backend/app/models/membership.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/receipt.py`
- Modify: `backend/app/models/policy_rule.py`
- Modify: `backend/app/models/employee.py`
- Test: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_models.py`:

```python
import uuid

from sqlalchemy import select

from app.models import Employee, Membership, Receipt, Tenant


async def test_create_tenant_and_membership(db_session):
    uid = uuid.uuid4()
    tenant = Tenant(name="Acme Inc", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()

    db_session.add(Membership(tenant_id=tenant.id, user_id=uid, role="owner"))
    await db_session.flush()

    rows = (await db_session.execute(select(Membership))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant.id
    assert rows[0].role == "owner"


async def test_receipt_requires_tenant_and_submitter(db_session):
    uid = uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()

    receipt = Receipt(
        image_path="acme/abc.png",
        tenant_id=tenant.id,
        submitted_by_user_id=uid,
        status="pending",
    )
    db_session.add(receipt)
    await db_session.flush()
    assert receipt.id is not None


async def test_employee_email_unique_per_tenant_only(db_session):
    uid = uuid.uuid4()
    t1 = Tenant(name="One", created_by_user_id=uid)
    t2 = Tenant(name="Two", created_by_user_id=uid)
    db_session.add_all([t1, t2])
    await db_session.flush()

    db_session.add(Employee(name="Bob", email="bob@x.com", tenant_id=t1.id))
    db_session.add(Employee(name="Bob", email="bob@x.com", tenant_id=t2.id))
    await db_session.flush()  # same email, different tenant — allowed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Membership' from 'app.models'`.

- [ ] **Step 3: Create the `Tenant` model**

Create `backend/app/models/tenant.py`:

```python
from datetime import datetime

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_by_user_id: Mapped[Uuid] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
```

- [ ] **Step 4: Create the `Membership` model**

Create `backend/app/models/membership.py`:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    user_id: Mapped[Uuid] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        Index("idx_memberships_user", "user_id"),
        Index("idx_memberships_tenant", "tenant_id"),
    )
```

- [ ] **Step 5: Update `models/__init__.py`**

Replace `backend/app/models/__init__.py` with:

```python
from app.models.employee import Employee
from app.models.membership import Membership
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt
from app.models.tenant import Tenant

__all__ = ["Employee", "Receipt", "FraudFlag", "PolicyRule", "Tenant", "Membership"]

from app.models.fraud_flag import FraudFlag  # noqa: E402  — after Receipt to resolve relationship
```

- [ ] **Step 6: Add `tenant_id` + `submitted_by_user_id` to `Receipt`**

In `backend/app/models/receipt.py`:

Change the import line `from sqlalchemy import ForeignKey, Index, Numeric, String, Text` to:

```python
from sqlalchemy import ForeignKey, Index, Numeric, String, Text, Uuid
```

Add these two `mapped_column`s immediately after the `employee_id` line (line ~15):

```python
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    submitted_by_user_id: Mapped[Uuid] = mapped_column(Uuid)
```

Add one index to the existing `__table_args__` tuple:

```python
    __table_args__ = (
        Index("idx_receipts_employee", "employee_id"),
        Index("idx_receipts_image_hash", "image_hash"),
        Index("idx_receipts_status", "status"),
        Index("idx_receipts_tenant", "tenant_id"),
    )
```

- [ ] **Step 7: Add `tenant_id` to `PolicyRule`**

In `backend/app/models/policy_rule.py`:

Change `from sqlalchemy import String` to `from sqlalchemy import ForeignKey, Index, String`.

Add after the `id` line:

```python
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
```

Add at the end of the class body:

```python
    __table_args__ = (Index("idx_policy_rules_tenant", "tenant_id"),)
```

- [ ] **Step 8: Add `tenant_id` to `Employee` and change email uniqueness**

Replace `backend/app/models/employee.py` with:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="employee")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_employees_tenant_email"),
        Index("idx_employees_tenant", "tenant_id"),
    )
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 10: Run the full suite so far**

Run: `cd backend && pytest -v`
Expected: PASS (all of test_auth.py + test_models.py).

- [ ] **Step 11: Commit**

```bash
cd backend && git add app/models tests/test_models.py
git commit -m "feat: add Tenant and Membership models, tenant_id on domain tables"
```

---

## Task 4: Alembic migration

**Files:**
- Create: `backend/alembic/versions/a1b2c3d4e5f6_multi_tenant.py`
- Modify: `backend/alembic/env.py`

- [ ] **Step 1: Point Alembic at the direct connection**

In `backend/alembic/env.py`, change the line:

```python
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```

to:

```python
config.set_main_option(
    "sqlalchemy.url", settings.DATABASE_URL_DIRECT or settings.DATABASE_URL
)
```

- [ ] **Step 2: Create the migration file**

Create `backend/alembic/versions/a1b2c3d4e5f6_multi_tenant.py` with exactly this content:

```python
"""multi tenant

Revision ID: a1b2c3d4e5f6
Revises: 210bbc94ba38
Create Date: 2026-09-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "210bbc94ba38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )
    op.create_index("idx_memberships_user", "memberships", ["user_id"])
    op.create_index("idx_memberships_tenant", "memberships", ["tenant_id"])

    # Existing dev rows (if any) are incompatible with the new NOT NULL columns.
    op.execute("DELETE FROM fraud_flags")
    op.execute("DELETE FROM receipts")
    op.execute("DELETE FROM policy_rules")
    op.execute("DELETE FROM employees")

    op.add_column("receipts", sa.Column("tenant_id", sa.Integer(), nullable=False))
    op.add_column(
        "receipts", sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False)
    )
    op.create_foreign_key(
        "fk_receipts_tenant", "receipts", "tenants", ["tenant_id"], ["id"]
    )
    op.create_index("idx_receipts_tenant", "receipts", ["tenant_id"])

    op.add_column("policy_rules", sa.Column("tenant_id", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "fk_policy_rules_tenant", "policy_rules", "tenants", ["tenant_id"], ["id"]
    )
    op.create_index("idx_policy_rules_tenant", "policy_rules", ["tenant_id"])

    op.add_column("employees", sa.Column("tenant_id", sa.Integer(), nullable=False))
    op.drop_constraint("employees_email_key", "employees", type_="unique")
    op.create_unique_constraint(
        "uq_employees_tenant_email", "employees", ["tenant_id", "email"]
    )
    op.create_foreign_key(
        "fk_employees_tenant", "employees", "tenants", ["tenant_id"], ["id"]
    )
    op.create_index("idx_employees_tenant", "employees", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("idx_employees_tenant", table_name="employees")
    op.drop_constraint("fk_employees_tenant", "employees", type_="foreignkey")
    op.drop_constraint("uq_employees_tenant_email", "employees", type_="unique")
    op.create_unique_constraint("employees_email_key", "employees", ["email"])
    op.drop_column("employees", "tenant_id")

    op.drop_index("idx_policy_rules_tenant", table_name="policy_rules")
    op.drop_constraint("fk_policy_rules_tenant", "policy_rules", type_="foreignkey")
    op.drop_column("policy_rules", "tenant_id")

    op.drop_index("idx_receipts_tenant", table_name="receipts")
    op.drop_constraint("fk_receipts_tenant", "receipts", type_="foreignkey")
    op.drop_column("receipts", "submitted_by_user_id")
    op.drop_column("receipts", "tenant_id")

    op.drop_index("idx_memberships_tenant", table_name="memberships")
    op.drop_index("idx_memberships_user", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("tenants")
```

- [ ] **Step 3: Verify the migration matches the models**

Run: `cd backend && DATABASE_URL_DIRECT=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test alembic upgrade head`
Expected: `Running upgrade 210bbc94ba38 -> a1b2c3d4e5f6, multi tenant` with no error.

Then run: `cd backend && DATABASE_URL_DIRECT=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test alembic check`
Expected: `No new upgrade operations detected.` (models and schema agree). If it reports differences, reconcile the migration with the models before continuing.

- [ ] **Step 4: Roll back down and back up once**

Run: `cd backend && DATABASE_URL_DIRECT=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test alembic downgrade base`
Expected: no error.
Run: `cd backend && DATABASE_URL_DIRECT=postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test alembic upgrade head`
Expected: no error. (Leaves the test DB migrated; the pytest fixtures drop/recreate anyway.)

- [ ] **Step 5: Commit**

```bash
cd backend && git add alembic/env.py alembic/versions/a1b2c3d4e5f6_multi_tenant.py
git commit -m "feat: alembic migration for tenants, memberships, tenant_id columns"
```

---

## Task 5: `get_current_context` dependency (active-tenant resolution)

**Files:**
- Modify: `backend/app/auth.py`
- Test: `backend/tests/test_auth.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_auth.py`:

```python
import uuid as _uuid

from app.auth import RequestContext, get_current_context
from app.models import Membership, Tenant


@pytest.fixture
def ctx_app():
    from fastapi import FastAPI as _FastAPI

    probe = _FastAPI()

    @probe.get("/ctx")
    async def ctx(context: RequestContext = Depends(get_current_context)):
        return {
            "user_id": str(context.user_id),
            "tenant_id": context.tenant_id,
            "role": context.role,
        }

    return probe


@pytest.fixture
async def ctx_client(ctx_app, db_session):
    from app.database import get_db

    async def _override():
        yield db_session

    ctx_app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=ctx_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    ctx_app.dependency_overrides.clear()


async def test_context_resolves_membership(ctx_client, db_session):
    uid = _uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=uid, role="owner"))
    await db_session.commit()

    token = make_token(str(uid))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(uid), "tenant_id": tenant.id, "role": "owner"}


async def test_context_missing_header_is_400(ctx_client):
    token = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get("/ctx", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


async def test_context_non_member_is_403(ctx_client, db_session):
    owner = _uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=owner)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=owner, role="owner"))
    await db_session.commit()

    outsider = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {outsider}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 403


async def test_context_bad_header_value_is_400(ctx_client):
    token = make_token(str(_uuid.uuid4()))
    resp = await ctx_client.get(
        "/ctx",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "not-an-int"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'RequestContext' from 'app.auth'`.

- [ ] **Step 3: Extend `app/auth.py`**

Append to `backend/app/auth.py`:

```python
from fastapi import Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Membership


@dataclass
class RequestContext:
    user_id: UUID
    email: str | None
    tenant_id: int
    role: str


async def get_current_context(
    user: CurrentUser = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db),
) -> RequestContext:
    if x_tenant_id is None:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")
    try:
        tenant_id = int(x_tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="X-Tenant-ID must be an integer"
        ) from exc

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=403, detail="Not a member of the requested business"
        )

    return RequestContext(
        user_id=user.user_id,
        email=user.email,
        tenant_id=tenant_id,
        role=membership.role,
    )
```

Move the new `from fastapi import Header` and `from sqlalchemy...` imports to the top import block of the file if you prefer; leaving them mid-file is valid Python and keeps this diff contained.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/auth.py tests/test_auth.py
git commit -m "feat: add get_current_context active-tenant resolver"
```

---

## Task 6: Pydantic schemas for tenants and `/auth/me`

**Files:**
- Create: `backend/app/schemas/tenant.py`
- Test: `backend/tests/test_tenant_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tenant_schemas.py`:

```python
from app.schemas.tenant import (
    MeResponse,
    MemberAddRequest,
    MembershipInfo,
    TenantCreate,
    TenantResponse,
)


def test_tenant_create_requires_name():
    obj = TenantCreate(name="Acme")
    assert obj.name == "Acme"


def test_member_add_request_normalises_email():
    obj = MemberAddRequest(email="Bob@Example.com")
    assert obj.email == "bob@example.com"


def test_me_response_shape():
    me = MeResponse(
        user_id="11111111-1111-1111-1111-111111111111",
        email="a@b.com",
        memberships=[MembershipInfo(tenant_id=1, name="Acme", role="owner")],
    )
    assert me.memberships[0].name == "Acme"


def test_tenant_response_from_attributes():
    class Row:
        id = 5
        name = "Acme"
        created_at = __import__("datetime").datetime(2026, 1, 1)

    out = TenantResponse.model_validate(Row())
    assert out.id == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_tenant_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.tenant'`.

- [ ] **Step 3: Write the schemas**

Create `backend/app/schemas/tenant.py`:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class TenantCreate(BaseModel):
    name: str


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime


class MemberAddRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class MembershipInfo(BaseModel):
    tenant_id: int
    name: str
    role: str


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    role: str


class MeResponse(BaseModel):
    user_id: UUID
    email: str | None
    memberships: list[MembershipInfo]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_tenant_schemas.py -v`
Expected: PASS (4 passed). If `EmailStr` raises an import error, run `pip install "pydantic[email]"` and add `pydantic[email]` to `requirements.txt`, then re-run.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/schemas/tenant.py tests/test_tenant_schemas.py requirements.txt
git commit -m "feat: add tenant and auth/me pydantic schemas"
```

---

## Task 7: Supabase REST client (`app/supabase_client.py`)

**Files:**
- Create: `backend/app/supabase_client.py`
- Test: `backend/tests/test_supabase_client.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_supabase_client.py`:

```python
import httpx
import pytest

from app import supabase_client


@pytest.fixture
def mock_transport(monkeypatch):
    calls = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["last"] = request
        if "/auth/v1/admin/users" in request.url.path:
            if "known@x.com" in str(request.url):
                return httpx.Response(
                    200,
                    json={"users": [{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}]},
                )
            return httpx.Response(200, json={"users": []})
        if request.method in ("PUT", "POST") and "/storage/v1/object/" in request.url.path:
            return httpx.Response(200, json={"Key": "receipts/1/x.png"})
        if request.method == "DELETE" and "/storage/v1/object/" in request.url.path:
            return httpx.Response(200, json={})
        return httpx.Response(404)

    monkeypatch.setattr(
        supabase_client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    return calls


async def test_lookup_known_email_returns_uuid(mock_transport):
    uid = await supabase_client.lookup_user_id_by_email("known@x.com")
    assert str(uid) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


async def test_lookup_unknown_email_returns_none(mock_transport):
    assert await supabase_client.lookup_user_id_by_email("nobody@x.com") is None


async def test_upload_object_posts_bytes(mock_transport):
    await supabase_client.upload_object("1/x.png", b"data", "image/png")
    req = mock_transport["last"]
    assert req.method == "PUT"
    assert "/storage/v1/object/receipts/1/x.png" in req.url.path


async def test_delete_object(mock_transport):
    await supabase_client.delete_object("1/x.png")
    req = mock_transport["last"]
    assert req.method == "DELETE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_supabase_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.supabase_client'`.

- [ ] **Step 3: Write the client**

Create `backend/app/supabase_client.py`:

```python
from uuid import UUID

import httpx

from app.config import settings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.SUPABASE_URL,
        headers={
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        },
        timeout=15.0,
    )


async def lookup_user_id_by_email(email: str) -> UUID | None:
    """Return the auth user id for an email, or None if no such account exists."""
    async with _client() as c:
        resp = await c.get("/auth/v1/admin/users", params={"email": email})
    resp.raise_for_status()
    body = resp.json()
    users = body.get("users", body if isinstance(body, list) else [])
    for user in users:
        if user.get("email", email).lower() == email.lower() or "email" not in user:
            return UUID(user["id"])
    return None


async def upload_object(path: str, data: bytes, content_type: str) -> None:
    """Upload bytes to the configured Storage bucket at `path` (no leading slash)."""
    bucket = settings.SUPABASE_STORAGE_BUCKET
    async with _client() as c:
        resp = await c.put(
            f"/storage/v1/object/{bucket}/{path}",
            content=data,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f"Storage upload failed: {resp.status_code} {resp.text}")


async def delete_object(path: str) -> None:
    bucket = settings.SUPABASE_STORAGE_BUCKET
    async with _client() as c:
        await c.delete(f"/storage/v1/object/{bucket}/{path}")
```

Note: the test overrides `supabase_client._client` with a `MockTransport`-backed client, so the real HTTP path is never exercised in CI.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_supabase_client.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/supabase_client.py tests/test_supabase_client.py
git commit -m "feat: add Supabase admin + storage REST client"
```

---

## Task 8: Tenants router + `main.py` wiring

**Files:**
- Create: `backend/app/api/v1/tenants.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_tenants.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tenants.py`:

```python
import uuid

import pytest

from app import supabase_client
from app.models import Membership, Tenant
from tests.conftest import make_token


async def _seed_tenant(db_session, owner_id: uuid.UUID, name="Acme"):
    tenant = Tenant(name=name, created_by_user_id=owner_id)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=owner_id, role="owner"))
    await db_session.commit()
    return tenant


async def test_create_tenant_makes_owner_membership(client, db_session, user_a_id):
    token = make_token(user_a_id)
    resp = await client.post(
        "/api/v1/tenants", json={"name": "Acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(Membership).where(Membership.tenant_id == tid)
    )).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].user_id) == user_a_id
    assert rows[0].role == "owner"


async def test_list_tenants_only_returns_own(client, db_session, user_a_id, user_b_id):
    await _seed_tenant(db_session, uuid.UUID(user_a_id), "AceCo")
    await _seed_tenant(db_session, uuid.UUID(user_b_id), "BeeCo")

    token = make_token(user_a_id)
    resp = await client.get(
        "/api/v1/tenants", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert names == {"AceCo"}


async def test_add_member_with_known_email(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    new_id = uuid.uuid4()

    async def fake_lookup(email):
        return new_id

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)

    token = make_token(user_a_id)
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/members",
        json={"email": "new@x.com"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 201

    rows = (await db_session.execute(
        __import__("sqlalchemy").select(Membership).where(
            Membership.tenant_id == tenant.id
        )
    )).scalars().all()
    assert {str(r.user_id) for r in rows} == {user_a_id, str(new_id)}


async def test_add_member_unknown_email_is_404(client, db_session, user_a_id, monkeypatch):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))

    async def fake_lookup(email):
        return None

    monkeypatch.setattr(supabase_client, "lookup_user_id_by_email", fake_lookup)

    token = make_token(user_a_id)
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/members",
        json={"email": "ghost@x.com"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 404


async def test_non_member_cannot_list_members(client, db_session, user_a_id, user_b_id):
    tenant = await _seed_tenant(db_session, uuid.UUID(user_a_id))
    token = make_token(user_b_id)
    resp = await client.get(
        f"/api/v1/tenants/{tenant.id}/members",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant.id)},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_tenants.py -v`
Expected: FAIL — 404s on every route (router not mounted).

- [ ] **Step 3: Write the router**

Create `backend/app/api/v1/tenants.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import supabase_client
from app.auth import CurrentUser, RequestContext, get_current_context, get_current_user
from app.database import get_db
from app.models import Membership, Tenant
from app.schemas.tenant import (
    MemberAddRequest,
    MemberResponse,
    TenantCreate,
    TenantResponse,
)

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    tenant = Tenant(name=body.name, created_by_user_id=user.user_id)
    db.add(tenant)
    await db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.user_id, role="owner"))
    await db.flush()
    return tenant


@router.get("", response_model=list[TenantResponse])
async def list_my_tenants(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Tenant]:
    result = await db.execute(
        select(Tenant)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Tenant.name)
    )
    return list(result.scalars().all())


async def _require_membership(tenant_id: int, context: RequestContext) -> None:
    if context.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="X-Tenant-ID does not match the tenant in the path",
        )


@router.get("/{tenant_id}/members", response_model=list[MemberResponse])
async def list_members(
    tenant_id: int,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> list[Membership]:
    await _require_membership(tenant_id, context)
    result = await db.execute(
        select(Membership).where(Membership.tenant_id == tenant_id)
    )
    return list(result.scalars().all())


@router.post("/{tenant_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    tenant_id: int,
    body: MemberAddRequest,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> Membership:
    await _require_membership(tenant_id, context)
    user_id = await supabase_client.lookup_user_id_by_email(body.email)
    if user_id is None:
        raise HTTPException(
            status_code=404,
            detail="No account for that email; ask them to sign up first",
        )
    existing = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    membership = Membership(tenant_id=tenant_id, user_id=user_id, role="member")
    db.add(membership)
    await db.flush()
    return membership


@router.delete("/{tenant_id}/members/{user_id}", status_code=204)
async def remove_member(
    tenant_id: int,
    user_id: str,
    context: RequestContext = Depends(get_current_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_membership(tenant_id, context)
    row = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.flush()
```

- [ ] **Step 4: Mount the router**

In `backend/app/main.py`, change:

```python
from app.api.v1 import receipts
```

to:

```python
from app.api.v1 import receipts, tenants
```

and add after `app.include_router(receipts.router, prefix="/api/v1")`:

```python
app.include_router(tenants.router, prefix="/api/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_tenants.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/api/v1/tenants.py app/main.py tests/test_tenants.py
git commit -m "feat: tenants and members API"
```

---

## Task 9: `/auth/me` router

**Files:**
- Create: `backend/app/api/v1/auth_routes.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_auth_me.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_auth_me.py`:

```python
import uuid

from app.models import Membership, Tenant
from tests.conftest import make_token


async def test_me_lists_memberships(client, db_session, user_a_id):
    uid = uuid.UUID(user_a_id)
    t1 = Tenant(name="AceCo", created_by_user_id=uid)
    t2 = Tenant(name="BeeCo", created_by_user_id=uid)
    db_session.add_all([t1, t2])
    await db_session.flush()
    db_session.add_all([
        Membership(tenant_id=t1.id, user_id=uid, role="owner"),
        Membership(tenant_id=t2.id, user_id=uid, role="member"),
    ])
    await db_session.commit()

    token = make_token(user_a_id, "a@b.com")
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "a@b.com"
    got = {(m["name"], m["role"]) for m in body["memberships"]}
    assert got == {("AceCo", "owner"), ("BeeCo", "member")}


async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth_me.py -v`
Expected: FAIL — 404 on `/api/v1/auth/me`.

- [ ] **Step 3: Write the router**

Create `backend/app/api/v1/auth_routes.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Membership, Tenant
from app.schemas.tenant import MeResponse, MembershipInfo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    result = await db.execute(
        select(Tenant.id, Tenant.name, Membership.role)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Tenant.name)
    )
    memberships = [
        MembershipInfo(tenant_id=row.id, name=row.name, role=row.role)
        for row in result.all()
    ]
    return MeResponse(user_id=user.user_id, email=user.email, memberships=memberships)
```

- [ ] **Step 4: Mount the router**

In `backend/app/main.py`, change the import to:

```python
from app.api.v1 import auth_routes, receipts, tenants
```

and add:

```python
app.include_router(auth_routes.router, prefix="/api/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth_me.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/api/v1/auth_routes.py app/main.py tests/test_auth_me.py
git commit -m "feat: add /api/v1/auth/me endpoint"
```

---

## Task 10: Retrofit the receipts router with tenant scoping + Supabase Storage

**Files:**
- Modify: `backend/app/api/v1/receipts.py`
- Modify: `backend/app/schemas/receipt.py`
- Test: `backend/tests/test_receipts_scoping.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_receipts_scoping.py`:

```python
import io
import uuid

import pytest

from app import supabase_client
from app.models import Membership, Receipt, Tenant
from tests.conftest import make_token


@pytest.fixture(autouse=True)
def _mock_storage(monkeypatch):
    async def _upload(path, data, content_type):
        return None

    async def _delete(path):
        return None

    monkeypatch.setattr(supabase_client, "upload_object", _upload)
    monkeypatch.setattr(supabase_client, "delete_object", _delete)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_receipts_scoping.py -v`
Expected: FAIL — uploads 500/201 without `tenant_id` in the response, 404 assertions fail.

- [ ] **Step 3: Add fields to the response schema**

In `backend/app/schemas/receipt.py`, in `ReceiptResponse`, add these two lines immediately after `id: int`:

```python
    tenant_id: int
    submitted_by_user_id: str
```

(Place `submitted_by_user_id` as `str`; Pydantic serialises the UUID to a string in the response.)

- [ ] **Step 4: Rewrite the receipts router**

Replace `backend/app/api/v1/receipts.py` with:

```python
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import supabase_client
from app.auth import RequestContext, get_current_context
from app.config import settings
from app.database import get_db
from app.models.receipt import Receipt
from app.schemas.receipt import ReceiptListResponse, ReceiptResponse

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
    scoped to the caller's active business."""
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

    object_path = f"{context.tenant_id}/{uuid.uuid4().hex}{ext}"
    try:
        await supabase_client.upload_object(
            object_path, contents, CONTENT_TYPES[ext]
        )
    except Exception as exc:  # storage failure — do not create a row
        logger.error("Storage upload failed for %s: %s", object_path, exc)
        raise HTTPException(status_code=502, detail="File storage failed") from exc

    receipt = Receipt(
        image_path=object_path,
        tenant_id=context.tenant_id,
        submitted_by_user_id=context.user_id,
        status="pending",
    )
    db.add(receipt)
    try:
        await db.flush()
    except Exception:
        await supabase_client.delete_object(object_path)
        raise
    await db.refresh(receipt, attribute_names=["fraud_flags"])

    logger.info(
        "Created receipt %s for tenant %s (%d bytes)",
        receipt.id, context.tenant_id, len(contents),
    )
    return receipt


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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_receipts_scoping.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the entire suite**

Run: `cd backend && pytest -v`
Expected: PASS — every test file green.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/api/v1/receipts.py app/schemas/receipt.py tests/test_receipts_scoping.py
git commit -m "feat: scope receipts endpoints by tenant, store files in Supabase Storage"
```

---

## Task 11: Point the app at Supabase and smoke-test manually

**Files:** none (configuration + manual verification)

- [ ] **Step 1: Fill in `.env`**

In `backend/.env` (create from `.env.example` if absent), set real values from the Supabase dashboard:
- `DATABASE_URL` → Settings → Database → Connection string → **Transaction pooler** (port 6543), with `postgresql+asyncpg://` scheme
- `DATABASE_URL_DIRECT` → the **direct** connection (port 5432), same scheme
- `SUPABASE_URL` → Settings → API → Project URL
- `SUPABASE_JWT_SECRET` → Settings → API → JWT Settings → JWT Secret
- `SUPABASE_SERVICE_ROLE_KEY` → Settings → API → `service_role` key

- [ ] **Step 2: Create the Storage bucket**

In the Supabase dashboard → Storage → New bucket → name `receipts`, **not** public. No policies needed (FastAPI uses the service-role key).

- [ ] **Step 3: Run migrations against Supabase**

Run: `cd backend && alembic upgrade head`
Expected: `Running upgrade 210bbc94ba38 -> a1b2c3d4e5f6`. Confirm in the dashboard that `tenants` and `memberships` tables exist and `receipts` has `tenant_id` + `submitted_by_user_id`.

- [ ] **Step 4: Create a test user in Supabase**

Dashboard → Authentication → Add user → email + password. Copy the user's UUID.

- [ ] **Step 5: Get a real access token**

Run (substitute the anon key, email, password):

```bash
curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: <ANON_KEY>" -H "Content-Type: application/json" \
  -d '{"email":"<EMAIL>","password":"<PASSWORD>"}' | python3 -m json.tool
```

Copy `access_token` from the response.

- [ ] **Step 6: Start the API**

Run: `cd backend && uvicorn app.main:app --reload`
Expected: starts with no error, connects to Supabase Postgres.

- [ ] **Step 7: Exercise the flow**

```bash
TOKEN=<access_token>

# create a business
curl -s -X POST localhost:8000/api/v1/tenants -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"Test Biz"}'

# note the returned id, then:
curl -s localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"

# upload a receipt (use any small PNG)
curl -s -X POST localhost:8000/api/v1/receipts/upload \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: <TENANT_ID>" \
  -F "file=@/path/to/receipt.png"

# list
curl -s "localhost:8000/api/v1/receipts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: <TENANT_ID>"
```

Expected: create returns 201 with an `id`; `/auth/me` lists the membership as `owner`; upload returns 201 with `image_path` like `<tenant_id>/<hex>.png`; the object appears in the Storage `receipts` bucket; list returns `total: 1`.

- [ ] **Step 8: Negative check**

Repeat the receipts list call with `X-Tenant-ID` omitted → expect `400`; with a tenant id you are not a member of → expect `403`.

- [ ] **Step 9: Commit any `.env.example` tweaks discovered during setup**

```bash
cd backend && git add .env.example
git commit -m "docs: refine .env.example from Supabase setup" || echo "nothing to commit"
```

---

## Task 12: Update project docs

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`

- [ ] **Step 1: Update the README**

In `README.md`, under "What's Built", add a row to the endpoints table and a short "Auth & multi-tenancy" paragraph:

```markdown
### Auth & multi-tenancy

- Authentication is handled by Supabase Auth (GoTrue). The frontend logs in
  against Supabase and sends the resulting JWT as `Authorization: Bearer <token>`.
- Every data request also carries `X-Tenant-ID: <id>` to select the active
  business. FastAPI verifies the JWT, checks membership, and scopes all queries
  by that tenant.
- Create a business with `POST /api/v1/tenants`; add teammates (who must already
  have a Supabase account) with `POST /api/v1/tenants/{id}/members`.
```

Add these rows to the endpoints table:

```markdown
| `GET` | `/api/v1/auth/me` | Current user + business memberships |
| `POST` | `/api/v1/tenants` | Create a business |
| `GET` | `/api/v1/tenants` | List your businesses |
| `GET` | `/api/v1/tenants/{id}/members` | List members |
| `POST` | `/api/v1/tenants/{id}/members` | Add a member by email |
| `DELETE` | `/api/v1/tenants/{id}/members/{user_id}` | Remove a member |
```

- [ ] **Step 2: Update TODO.md**

In `TODO.md`, under "Done ✅", add:

```markdown
- [x] Supabase-backed multi-tenant auth foundation (tenants, memberships, JWT, Storage)
```

And add a new near-term section after "Today (Getting Started)":

```markdown
## Next (Phase 2 hardening of multi-tenancy)
- [ ] Add Row-Level Security policies on tenant tables (defense-in-depth)
- [ ] Enforce roles (admin vs member) on tenant + policy_rule endpoints
- [ ] Invitation tokens + emails for users without an account yet
- [ ] Move Storage reads to signed URLs for the frontend
```

- [ ] **Step 3: Commit**

```bash
git add README.md TODO.md
git commit -m "docs: document Supabase multi-tenant auth"
```

- [ ] **Step 4: Run the full suite one last time**

Run: `cd backend && pytest -v`
Expected: all green.

---

## Self-Review

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| Supabase = data/auth/storage, FastAPI = processing | Architecture — Tasks 2, 7, 10 |
| Single access path (all data via FastAPI) | Tasks 8, 9, 10 |
| No RLS this phase | Explicitly deferred (Task 12 Step 2 backlog) |
| `tenants` table | Tasks 3, 4 |
| `memberships` table (multi-business, `role` default `owner`) | Tasks 3, 4 |
| `receipts.tenant_id` + `submitted_by_user_id` | Tasks 3, 4, 10 |
| `policy_rules.tenant_id` | Tasks 3, 4 |
| `employees.tenant_id` + `(tenant_id, email)` unique | Tasks 3, 4 |
| `fraud_flags` unchanged | (no task needed — confirmed) |
| Migration with no backfill | Task 4 |
| Alembic on direct connection, runtime on pooled | Tasks 1 (settings), 4 (env.py), 11 |
| JWT verification (HS256, `SUPABASE_JWT_SECRET`, `aud`, `exp`) | Task 2 |
| `X-Tenant-ID` header → membership lookup → `RequestContext` | Task 5 |
| New settings / `.env.example` | Task 1 |
| New deps (`python-jose`, `httpx`) | Task 1 |
| `GET /api/v1/auth/me` | Task 9 |
| `POST/GET /api/v1/tenants`, members sub-routes | Task 8 |
| Add member by existing email, 404 if no account | Task 8 |
| Receipts endpoints gain context scoping | Task 10 |
| Upload sets tenant/submitter from context, ignores client values | Task 10 |
| `GET /{id}` cross-tenant → 404 | Task 10 |
| List always filtered by tenant | Task 10 |
| Supabase Storage for images, path `{tenant_id}/{uuid}.ext` | Tasks 7, 10 |
| Storage upload failure → 502, no row | Task 10 |
| Error codes: 401 / 403 / 400 / 404 | Tasks 2, 5, 8, 10 |
| pytest harness, offline (mocked Supabase), two-tenant fixtures | Tasks 1, 7, 8, 10 |
| `alembic upgrade head` validated once manually | Tasks 4, 11 |
| Out of scope: RLS, roles, invites, fraud pipeline, frontend | Not implemented; backlog added in Task 12 |

No gaps.

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" left. Every code step shows complete code; every run step shows the command and expected output.

**Type consistency:**
- `CurrentUser(user_id: UUID, email: str | None)` — defined Task 2, used Tasks 5, 8, 9.
- `RequestContext(user_id, email, tenant_id, role)` — defined Task 5, used Tasks 8, 10.
- `get_current_user` / `get_current_context` names consistent across Tasks 2, 5, 8, 9, 10.
- `supabase_client.lookup_user_id_by_email` / `upload_object` / `delete_object` — defined Task 7, monkeypatched and called with the same signatures in Tasks 8, 10.
- `make_token(user_id, email=..., expired=...)` — defined Task 1 conftest, used Tasks 2, 5, 8, 9, 10.
- Migration `revision`/`down_revision` (`a1b2c3d4e5f6` / `210bbc94ba38`) consistent between Task 4 file and validation commands.
- `Tenant` / `Membership` field names (`created_by_user_id`, `tenant_id`, `user_id`, `role`) identical across models (Task 3), migration (Task 4), routers (Tasks 8, 9), and tests.
