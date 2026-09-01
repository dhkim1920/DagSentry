"""Create durable Incident recovery notification storage.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_recovery_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_key", sa.String(length=64), nullable=False),
        sa.Column("delivery_key_version", sa.SmallInteger(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "DELIVERED",
                "FAILED",
                "SUPPRESSED",
                name="incident_recovery_notification_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
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
        sa.CheckConstraint(
            "delivery_key_version >= 1",
            name="ck_incident_recovery_notification_key_version",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_incident_recovery_notification_attempt_count",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key"),
        sa.UniqueConstraint("incident_id"),
    )
    op.create_index(
        "ix_incident_recovery_notifications_status_updated",
        "incident_recovery_notifications",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_recovery_notifications_status_updated",
        table_name="incident_recovery_notifications",
    )
    op.drop_table("incident_recovery_notifications")
