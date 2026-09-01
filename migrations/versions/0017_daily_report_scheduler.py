"""Add Daily Report APScheduler configuration and execution state.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_report_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("run_at_local_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("revision >= 1", name="ck_daily_report_schedules_revision"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment", name="uq_daily_report_schedules_environment"),
    )
    op.create_table(
        "scheduler_heartbeats",
        sa.Column("scheduler_name", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("scheduler_name"),
    )
    op.create_table(
        "daily_report_schedule_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column(
            "trigger_type",
            sa.Enum(
                "SCHEDULED",
                "MANUAL",
                name="daily_report_schedule_run_trigger",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CLAIMED",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                "SKIPPED",
                name="daily_report_schedule_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("error_category", sa.String(length=50), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(trigger_type = 'SCHEDULED' AND schedule_id IS NOT NULL) OR trigger_type = 'MANUAL'",
            name="ck_daily_report_schedule_runs_schedule_shape",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["daily_report_schedules.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["report_id"], ["daily_reports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id",
            "report_date",
            "trigger_type",
            name="uq_daily_report_schedule_runs_scheduled_date",
        ),
    )
    op.create_index(
        "ix_daily_report_schedule_runs_status_scheduled_for",
        "daily_report_schedule_runs",
        ["status", "scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_report_schedule_runs_status_scheduled_for",
        table_name="daily_report_schedule_runs",
    )
    op.drop_table("daily_report_schedule_runs")
    op.drop_table("scheduler_heartbeats")
    op.drop_table("daily_report_schedules")
