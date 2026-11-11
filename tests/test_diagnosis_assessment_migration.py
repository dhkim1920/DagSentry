import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_assessment_upgrade_preserves_legacy_and_reused_rows() -> None:
    migration = importlib.import_module("migrations.versions.0023_diagnosis_assessment")
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    diagnoses = sa.Table(
        "diagnoses",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            diagnoses.insert(), [{"id": 1, "source": "AI"}, {"id": 2, "source": "REUSED"}]
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            updated = sa.Table("diagnoses", sa.MetaData(), autoload_with=connection)
            assert connection.execute(sa.select(updated.c.assessment)).scalars().all() == [
                None,
                None,
            ]
            connection.execute(
                updated.update().where(updated.c.id == 1).values(assessment={"basis": "INFERRED"})
            )
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        updated.update()
                        .where(updated.c.id == 2)
                        .values(assessment={"basis": "DIRECT"})
                    )
            migration.downgrade()
            assert "assessment" not in {
                column["name"] for column in sa.inspect(connection).get_columns("diagnoses")
            }
            assert connection.scalar(sa.select(sa.func.count()).select_from(diagnoses)) == 2
    engine.dispose()
