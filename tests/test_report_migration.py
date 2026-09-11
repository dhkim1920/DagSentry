import importlib
from datetime import date

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_daily_report import statistics


def test_v1_report_migration_and_non_utc_downgrade_preserve_data() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        verify_report_migration(engine)
    finally:
        engine.dispose()


def verify_report_migration(engine: sa.Engine) -> None:
    revision = importlib.import_module("migrations.versions.0020_daily_report_v2")
    metadata = sa.MetaData()
    reports = sa.Table(
        "daily_reports",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("statistics", sa.JSON),
        sa.Column("report_schema_version", sa.Integer),
    )
    metadata.create_all(engine)
    legacy = statistics().model_dump(mode="json", exclude={"top_failures"})
    legacy["schema_version"] = 1
    with engine.begin() as connection:
        connection.execute(
            reports.insert().values(id=1, statistics=legacy, report_schema_version=1)
        )
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            converted = connection.execute(sa.select(reports)).mappings().one()
            assert converted["statistics"] == {**legacy, "schema_version": 2, "top_failures": []}
            assert converted["report_schema_version"] == 2
            revision.downgrade()
            assert connection.scalar(sa.select(reports.c.statistics)) == legacy
            revision.upgrade()
            from dagsentry.domain.reporting import report_period

            start, end = report_period(date(2026, 8, 12), "Asia/Seoul")
            kst = {
                **converted["statistics"],
                "timezone": "Asia/Seoul",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
            }
            connection.execute(
                reports.insert().values(id=2, statistics=kst, report_schema_version=2)
            )
            with pytest.raises(RuntimeError, match="restore a backup"):
                revision.downgrade()
            assert connection.execute(
                sa.select(reports.c.report_schema_version)
            ).scalars().all() == [2, 2]
            assert (
                connection.scalar(sa.select(reports.c.statistics).where(reports.c.id == 2)) == kst
            )
