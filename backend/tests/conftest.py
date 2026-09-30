"""Shared fixtures for all tests."""
import os
import time
import uuid
from datetime import date
from decimal import Decimal

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SUPABASE_STORAGE_BUCKET", "receipts")
_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_detection_test",
)
if "test" not in _TEST_DB_URL.rsplit("/", 1)[-1]:
    raise RuntimeError(
        f"Refusing to run tests: database name in {_TEST_DB_URL!r} does not contain 'test'"
    )
os.environ["DATABASE_URL"] = _TEST_DB_URL

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database import Base, get_db
import app.models  # noqa: F401  — ensure all models are registered on Base.metadata
from app.main import app
from app.models.fraud_flag import FraudFlag
from app.models.policy_rule import PolicyRule
from app.models.receipt import Receipt

test_engine = create_async_engine(settings.DATABASE_URL, echo=False, poolclass=NullPool)
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


@pytest.fixture
def make_receipt():
    """Factory for Receipt ORM instances without a DB session."""
    def _make(
        id=1,
        merchant="Starbucks",
        amount=Decimal("15.47"),
        transaction_date=date(2024, 1, 15),
        category=None,
        employee_id=None,
        image_hash=None,
        status="analyzed",
        tenant_id=1,
    ):
        r = Receipt()
        r.id = id
        r.merchant = merchant
        r.amount = amount
        r.transaction_date = transaction_date
        r.category = category
        r.employee_id = employee_id
        r.image_hash = image_hash
        r.status = status
        r.tenant_id = tenant_id
        return r
    return _make


@pytest.fixture
def make_rule():
    """Factory for PolicyRule ORM instances."""
    def _make(rule_type, parameters, severity="medium", rule_name=None, id=1, tenant_id=1):
        rule = PolicyRule()
        rule.id = id
        rule.rule_name = rule_name or f"Test {rule_type} rule"
        rule.rule_type = rule_type
        rule.parameters = parameters
        rule.severity = severity
        rule.is_active = True
        rule.tenant_id = tenant_id
        return rule
    return _make


@pytest.fixture
def make_flag():
    """Factory for FraudFlag ORM instances."""
    def _make(flag_type="duplicate", severity="high", confidence=100.0, receipt_id=1):
        flag = FraudFlag()
        flag.flag_type = flag_type
        flag.severity = severity
        flag.confidence_score = Decimal(str(confidence))
        flag.receipt_id = receipt_id
        return flag
    return _make
