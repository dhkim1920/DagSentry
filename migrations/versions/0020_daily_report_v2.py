"""Preserve UTC meaning while converting stored daily reports to v2."""

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    reports = sa.Table("daily_reports", sa.MetaData(), autoload_with=connection)
    for row in (
        connection.execute(
            sa.select(reports.c.id, reports.c.statistics).where(
                reports.c.report_schema_version == 1
            )
        )
        .mappings()
        .all()
    ):
        statistics = dict(row["statistics"])
        statistics.update(schema_version=2, timezone="UTC", top_failures=[])
        connection.execute(
            reports.update()
            .where(reports.c.id == row["id"])
            .values(
                report_schema_version=2,
                statistics=statistics,
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    reports = sa.Table("daily_reports", sa.MetaData(), autoload_with=connection)
    rows = (
        connection.execute(
            sa.select(reports.c.id, reports.c.statistics).where(
                reports.c.report_schema_version == 2
            )
        )
        .mappings()
        .all()
    )
    if any(row["statistics"].get("timezone") != "UTC" for row in rows):
        raise RuntimeError(
            "Cannot downgrade non-UTC reports to v1; restore a backup with its matching image."
        )
    for row in rows:
        statistics = dict(row["statistics"])
        statistics["schema_version"] = 1
        statistics.pop("top_failures", None)
        connection.execute(
            reports.update()
            .where(reports.c.id == row["id"])
            .values(
                report_schema_version=1,
                statistics=statistics,
            )
        )
