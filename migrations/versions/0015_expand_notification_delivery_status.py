"""Expand Notification delivery status for SUPPRESSED.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=9),
            type_=sa.String(length=10),
            existing_nullable=False,
        )


def downgrade() -> None:
    # VARCHAR(9) cannot represent SUPPRESSED. Keep the row and payload while returning it to a
    # retryable legacy status before narrowing the column.
    op.execute(
        "UPDATE notification_deliveries "
        "SET status = 'FAILED', suppression_reason = NULL "
        "WHERE status = 'SUPPRESSED'"
    )
    with op.batch_alter_table("notification_deliveries") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=10),
            type_=sa.String(length=9),
            existing_nullable=False,
        )
