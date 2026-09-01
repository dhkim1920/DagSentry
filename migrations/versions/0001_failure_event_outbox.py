"""Create Failure Event and diagnosis outbox tables.

Revision ID: 0001
Revises:
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failure_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_key_version", sa.SmallInteger(), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("dag_id", sa.String(length=250), nullable=False),
        sa.Column("dag_run_id", sa.String(length=250), nullable=False),
        sa.Column("task_id", sa.String(length=250), nullable=False),
        sa.Column("map_index", sa.Integer(), nullable=False),
        sa.Column("try_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operator_type", sa.String(length=250), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("map_index >= -1", name="ck_failure_events_map_index"),
        sa.CheckConstraint("try_number >= 1", name="ck_failure_events_try_number"),
        sa.CheckConstraint("event_key_version >= 1", name="ck_failure_events_event_key_version"),
        sa.CheckConstraint(
            "source IN ('LISTENER', 'RETRY_CALLBACK', 'RECONCILER')",
            name="ck_failure_events_source",
        ),
        sa.CheckConstraint(
            "state IN ('FAILED', 'UP_FOR_RETRY')",
            name="ck_failure_events_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_failure_events_event_key"),
    )
    op.create_table(
        "diagnosis_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("failure_event_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=250), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint("attempt_count >= 0", name="ck_diagnosis_outbox_attempt_count"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'DEAD')",
            name="ck_diagnosis_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["failure_event_id"],
            ["failure_events.id"],
            name="fk_diagnosis_outbox_failure_event_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("failure_event_id", name="uq_diagnosis_outbox_failure_event_id"),
    )


def downgrade() -> None:
    op.drop_table("diagnosis_outbox")
    op.drop_table("failure_events")
