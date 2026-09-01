"""Create the Managed Connection vault metadata table.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "AIRFLOW",
                "LLM",
                "NOTIFICATION",
                name="managed_connection_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.Enum(
                "AIRFLOW",
                "OLLAMA",
                "OPENAI",
                "AZURE_OPENAI",
                "ANTHROPIC",
                "BEDROCK",
                "WEBHOOK",
                "SLACK",
                "TEAMS",
                "DISCORD",
                name="managed_connection_provider",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=250), nullable=False),
        sa.Column("non_secret_config", sa.JSON(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("secret_key_version", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_test_status",
            sa.Enum(
                "PASSED",
                "FAILED",
                name="managed_connection_test_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("last_test_error_category", sa.String(length=50), nullable=True),
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
        sa.CheckConstraint("version >= 1", name="ck_managed_connections_version"),
        sa.CheckConstraint(
            "(secret_ciphertext IS NULL AND secret_nonce IS NULL AND secret_key_version IS NULL) "
            "OR (secret_ciphertext IS NOT NULL AND secret_nonce IS NOT NULL "
            "AND secret_key_version IS NOT NULL)",
            name="ck_managed_connections_secret_shape",
        ),
        sa.CheckConstraint(
            "secret_ciphertext IS NULL OR length(secret_ciphertext) >= 16",
            name="ck_managed_connections_ciphertext_length",
        ),
        sa.CheckConstraint(
            "secret_nonce IS NULL OR length(secret_nonce) = 12",
            name="ck_managed_connections_nonce_length",
        ),
        sa.CheckConstraint(
            "secret_key_version IS NULL OR secret_key_version >= 1",
            name="ck_managed_connections_key_version",
        ),
        sa.CheckConstraint(
            "(purpose = 'AIRFLOW' AND provider = 'AIRFLOW') OR "
            "(purpose = 'LLM' AND provider IN "
            "('OLLAMA', 'OPENAI', 'AZURE_OPENAI', 'ANTHROPIC', 'BEDROCK')) OR "
            "(purpose = 'NOTIFICATION' AND provider IN "
            "('WEBHOOK', 'SLACK', 'TEAMS', 'DISCORD'))",
            name="ck_managed_connections_provider_purpose",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment",
            "purpose",
            name="uq_managed_connections_environment_purpose",
        ),
    )


def downgrade() -> None:
    op.drop_table("managed_connections")
