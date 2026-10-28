"""admin/member roles: rename owner to admin and constrain role values

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-28

"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE memberships SET role='admin' WHERE role='owner'")
    op.create_check_constraint(
        "ck_memberships_role", "memberships", "role IN ('admin','member')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.execute("UPDATE memberships SET role='owner' WHERE role='admin'")
