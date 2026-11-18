"""Guards against tables being exposed through Supabase's Data API."""
import os
import re
from pathlib import Path

import httpx
import pytest

import app.models  # noqa: F401
from app.database import Base

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def test_every_model_table_has_rls_enabled_in_a_migration():
    rls_migrations = [
        p.read_text()
        for p in _VERSIONS.glob("*.py")
        if "ENABLE ROW LEVEL SECURITY" in p.read_text()
    ]
    assert rls_migrations, "no migration enables RLS"
    missing = [
        table
        for table in Base.metadata.tables
        if not any(re.search(rf'["\']{table}["\']', text) for text in rls_migrations)
    ]
    assert not missing, f"tables without RLS enabled in a migration: {missing}"


@pytest.mark.skipif(
    not (os.environ.get("LIVE_SUPABASE_URL") and os.environ.get("LIVE_SUPABASE_ANON_KEY")),
    reason="set LIVE_SUPABASE_URL and LIVE_SUPABASE_ANON_KEY to probe a real project",
)
@pytest.mark.parametrize(
    "table",
    ["tenants", "memberships", "employees", "receipts", "fraud_flags", "policy_rules"],
)
def test_anon_key_cannot_read_table(table):
    url = os.environ["LIVE_SUPABASE_URL"].rstrip("/")
    key = os.environ["LIVE_SUPABASE_ANON_KEY"]
    resp = httpx.get(
        f"{url}/rest/v1/{table}?select=id",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=10,
    )
    assert resp.status_code in (401, 403) or resp.json() == []
