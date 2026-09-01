"""Bind a CSRF token to each user session.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=True),
    )
    # Existing opaque sessions cannot be assigned a recoverable CSRF secret safely.
    op.execute(sa.text("DELETE FROM user_sessions"))
    with op.batch_alter_table("user_sessions") as batch:
        batch.alter_column("csrf_token_hash", existing_type=sa.String(length=64), nullable=False)
        batch.create_check_constraint(
            "ck_user_sessions_csrf_hash_length",
            "length(csrf_token_hash) = 64",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_sessions") as batch:
        batch.drop_constraint("ck_user_sessions_csrf_hash_length", type_="check")
        batch.drop_column("csrf_token_hash")
