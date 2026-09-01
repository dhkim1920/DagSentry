"""Create versioned Error Signature table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "error_signatures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint_version", sa.SmallInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("operator_type", sa.String(length=250), nullable=True),
        sa.Column("exception_class", sa.String(length=500), nullable=True),
        sa.Column("vendor_error_code", sa.String(length=250), nullable=True),
        sa.Column("normalized_message", sa.Text(), nullable=True),
        sa.Column("application_stack_frame", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("fingerprint_version >= 1", name="ck_error_signatures_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fingerprint_version",
            "fingerprint",
            name="uq_error_signatures_version_fingerprint",
        ),
    )


def downgrade() -> None:
    op.drop_table("error_signatures")
