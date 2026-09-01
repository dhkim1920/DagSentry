"""Create Incident state transition audit storage.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statuses = ("OPEN", "ACKNOWLEDGED", "RECOVERED", "RESOLVED", "IGNORED")
    op.create_table(
        "incident_state_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "previous_status",
            sa.Enum(
                *statuses,
                name="incident_transition_previous_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                *statuses,
                name="incident_transition_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "initiator",
            sa.Enum(
                "OPERATOR",
                "SYSTEM",
                "AI",
                name="incident_transition_initiator",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=250), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_state_transitions_incident_created",
        "incident_state_transitions",
        ["incident_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_state_transitions_incident_created",
        table_name="incident_state_transitions",
    )
    op.drop_table("incident_state_transitions")
