"""Create Incident correlation storage.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_INCIDENT_PREDICATE = sa.text(
    "error_signature_id IS NOT NULL AND status IN ('OPEN', 'ACKNOWLEDGED')"
)


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("dag_id", sa.String(length=250), nullable=False),
        sa.Column("task_id", sa.String(length=250), nullable=False),
        sa.Column("error_signature_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "ACKNOWLEDGED",
                "RECOVERED",
                "RESOLVED",
                "IGNORED",
                name="incident_status",
                native_enum=False,
            ),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["error_signature_id"], ["error_signatures.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_incidents_active_correlation",
        "incidents",
        ["environment", "dag_id", "task_id", "error_signature_id"],
        unique=True,
        postgresql_where=_ACTIVE_INCIDENT_PREDICATE,
        sqlite_where=_ACTIVE_INCIDENT_PREDICATE,
    )
    op.create_table(
        "incident_failure_events",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("failure_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["failure_event_id"], ["failure_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("incident_id", "failure_event_id"),
        sa.UniqueConstraint("failure_event_id", name="uq_incident_failure_event"),
    )


def downgrade() -> None:
    op.drop_table("incident_failure_events")
    op.drop_index("uq_incidents_active_correlation", table_name="incidents")
    op.drop_table("incidents")
