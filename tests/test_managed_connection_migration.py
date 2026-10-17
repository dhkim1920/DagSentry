import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_smtp_upgrade_and_downgrade_preserve_connections() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        verify_smtp_migration(engine)
    finally:
        engine.dispose()


def verify_smtp_migration(engine: sa.Engine) -> None:
    revision = importlib.import_module("migrations.versions.0021_smtp_managed_connections")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Uuid, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            importlib.import_module("migrations.versions.0014_managed_connections").upgrade()
    connections = sa.Table("managed_connections", metadata, autoload_with=engine)
    legacy = {
        "id": "a" * 32,
        "environment": "test",
        "purpose": "NOTIFICATION",
        "provider": "SLACK",
        "display_name": "Existing connection",
        "non_secret_config": {"channel": "C123"},
        "enabled": True,
        "version": 3,
        "secret_ciphertext": b"x" * 32,
        "secret_nonce": b"n" * 12,
        "secret_key_version": 1,
    }
    with engine.begin() as connection:
        connection.execute(connections.insert().values(**legacy))
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            stored = connection.execute(sa.select(connections)).mappings().one()
            assert stored["secret_ciphertext"] == legacy["secret_ciphertext"]
            assert stored["version"] == 3
            revision.downgrade()
            revision.upgrade()
            connection.execute(connections.update().values(provider="SMTP"))
            with pytest.raises(RuntimeError, match="restore a backup"):
                revision.downgrade()
            assert connection.scalar(sa.select(connections.c.provider)) == "SMTP"
