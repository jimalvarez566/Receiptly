"""multi tenant

Revision ID: a1b2c3d4e5f6
Revises: 6d34720ba4d7
Create Date: 2026-09-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "6d34720ba4d7"
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
