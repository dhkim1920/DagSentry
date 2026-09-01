"""Add stable initial Failure and Notification suppression state.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("initial_failure_event_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE incidents SET initial_failure_event_id = ("
        "SELECT incident_failure_events.failure_event_id "
        "FROM incident_failure_events "
        "WHERE incident_failure_events.incident_id = incidents.id "
        "ORDER BY incident_failure_events.created_at, "
        "incident_failure_events.failure_event_id LIMIT 1)"
    )
    op.alter_column("incidents", "initial_failure_event_id", nullable=False)
    op.create_foreign_key(
        "fk_incidents_initial_failure_event",
        "incidents",
        "failure_events",
        ["initial_failure_event_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_incidents_initial_failure_event",
        "incidents",
        ["initial_failure_event_id"],
    )

    op.add_column(
        "notification_deliveries",
        sa.Column("suppression_reason", sa.String(length=50), nullable=True),
    )
    op.create_check_constraint(
        "ck_notification_delivery_status",
        "notification_deliveries",
        "status IN ('PENDING', 'DELIVERED', 'FAILED', 'SUPPRESSED')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_deliveries WHERE status = 'SUPPRESSED'")
    op.drop_constraint(
        "ck_notification_delivery_status",
        "notification_deliveries",
        type_="check",
    )
    op.drop_column("notification_deliveries", "suppression_reason")

    op.drop_constraint(
        "uq_incidents_initial_failure_event",
        "incidents",
        type_="unique",
    )
    op.drop_constraint(
        "fk_incidents_initial_failure_event",
        "incidents",
        type_="foreignkey",
    )
    op.drop_column("incidents", "initial_failure_event_id")
