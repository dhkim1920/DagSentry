"""Allow SMTP Managed Connections without changing stored secrets.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels = None
depends_on = None

_PROVIDERS = "'AIRFLOW', 'OLLAMA', 'OPENAI', 'AZURE_OPENAI', 'ANTHROPIC', 'BEDROCK', 'WEBHOOK', 'SLACK', 'TEAMS', 'DISCORD'"
_PURPOSE = (
    "(purpose = 'AIRFLOW' AND provider = 'AIRFLOW') OR "
    "(purpose = 'LLM' AND provider IN "
    "('OLLAMA', 'OPENAI', 'AZURE_OPENAI', 'ANTHROPIC', 'BEDROCK')) OR "
    "(purpose = 'NOTIFICATION' AND provider IN ('WEBHOOK', 'SLACK', 'TEAMS', 'DISCORD'{smtp}))"
)


def _constraints(*, smtp: bool) -> None:
    smtp_provider = ", 'SMTP'" if smtp else ""
    with op.batch_alter_table("managed_connections") as batch:
        batch.drop_constraint("managed_connection_provider", type_="check")
        batch.drop_constraint("ck_managed_connections_provider_purpose", type_="check")
        batch.create_check_constraint(
            "managed_connection_provider",
            f"provider IN ({_PROVIDERS}{smtp_provider})",
        )
        batch.create_check_constraint(
            "ck_managed_connections_provider_purpose",
            _PURPOSE.format(smtp=", 'SMTP'" if smtp else ""),
        )


def upgrade() -> None:
    _constraints(smtp=True)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM managed_connections WHERE provider = 'SMTP'")
    ):
        raise RuntimeError(
            "Cannot downgrade SMTP connections; restore a backup with its matching image."
        )
    _constraints(smtp=False)
