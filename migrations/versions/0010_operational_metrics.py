"""Create durable low-cardinality operational counters.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_metric_counters",
        sa.Column("metric_name", sa.String(length=100), nullable=False),
        sa.Column("label_value", sa.String(length=50), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("value >= 0", name="ck_operational_metric_counter_value"),
        sa.PrimaryKeyConstraint("metric_name", "label_value"),
    )


def downgrade() -> None:
    op.drop_table("operational_metric_counters")
