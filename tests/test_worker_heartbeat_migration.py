import importlib
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def verify_worker_heartbeat_migration(engine: sa.Engine) -> None:
    migration = importlib.import_module("migrations.versions.0022_worker_heartbeats")
    metadata = sa.MetaData()
    jobs = sa.Table("preserved_jobs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(jobs.insert().values(id=1))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            heartbeats = sa.Table(
                "worker_heartbeats",
                sa.MetaData(),
                sa.Column("instance_id", sa.Uuid(), primary_key=True),
                autoload_with=connection,
            )
            now = datetime.now(UTC)
            instance_id = uuid4()
            connection.execute(
                heartbeats.insert().values(
                    instance_id=instance_id,
                    started_at=now,
                    last_heartbeat_at=now,
                    stopped_at=None,
                    version="test",
                )
            )
            assert connection.scalar(sa.select(heartbeats.c.instance_id)) == instance_id
            migration.downgrade()
            assert "worker_heartbeats" not in sa.inspect(connection).get_table_names()
            assert connection.scalar(sa.select(jobs.c.id)) == 1


def test_worker_heartbeat_migration_upgrade_and_downgrade() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        verify_worker_heartbeat_migration(engine)
    finally:
        engine.dispose()
