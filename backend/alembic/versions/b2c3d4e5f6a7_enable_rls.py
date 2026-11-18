"""enable row-level security on all public tables

Supabase exposes the public schema through PostgREST, and the anon key ships
in the frontend bundle. With RLS off, that key can read every row. Enabling
RLS with no policies denies anon/authenticated everything. The backend is
unaffected: it connects as the table owner (postgres), which bypasses RLS
because FORCE ROW LEVEL SECURITY is deliberately not set.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-28

"""
from typing import Sequence, Union

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# New tables added by later migrations must enable RLS themselves;
# tests/test_rls_coverage.py fails if one is missed.
TABLES = [
    "tenants",
    "memberships",
    "employees",
    "receipts",
    "fraud_flags",
    "policy_rules",
    "alembic_version",
]


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
