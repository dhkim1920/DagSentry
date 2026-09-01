"""Create idempotent daily reports.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("report_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("statistics", sa.JSON(), nullable=False),
        sa.Column("rule_based_report", sa.JSON(), nullable=False),
        sa.Column("ai_summary", sa.JSON(), nullable=True),
        sa.Column("summary_provider", sa.String(length=50), nullable=True),
        sa.Column("delivery_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "DELIVERED",
                "FAILED",
                "SUPPRESSED",
                name="daily_report_delivery_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_category", sa.String(length=50), nullable=True),
        sa.Column("last_response_status", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_daily_reports_attempt_count"),
        sa.CheckConstraint(
            "report_schema_version >= 1",
            name="ck_daily_reports_schema_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key"),
        sa.UniqueConstraint(
            "report_date",
            "environment",
            name="uq_daily_reports_date_environment",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_reports")
