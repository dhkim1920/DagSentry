"""Add Daily Report presentation and delivery settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_report_schedules",
        sa.Column(
            "display_name", sa.String(length=120), nullable=False, server_default="Daily Report"
        ),
    )
    op.add_column(
        "daily_report_schedules",
        sa.Column(
            "report_title",
            sa.String(length=160),
            nullable=False,
            server_default="DagSentry 일일 장애 리포트",
        ),
    )
    op.add_column(
        "daily_report_schedules",
        sa.Column("notification_connection_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_daily_report_schedules_notification_connection",
        "daily_report_schedules",
        "managed_connections",
        ["notification_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "daily_report_schedules",
        sa.Column("use_ai_summary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("daily_report_schedules", "display_name", server_default=None)
    op.alter_column("daily_report_schedules", "report_title", server_default=None)
    op.alter_column("daily_report_schedules", "use_ai_summary", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_daily_report_schedules_notification_connection",
        "daily_report_schedules",
        type_="foreignkey",
    )
    op.drop_column("daily_report_schedules", "use_ai_summary")
    op.drop_column("daily_report_schedules", "notification_connection_id")
    op.drop_column("daily_report_schedules", "report_title")
    op.drop_column("daily_report_schedules", "display_name")
