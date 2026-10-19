"""Track Worker process liveness for the operational overview.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("instance_id"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
