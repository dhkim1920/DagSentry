import os
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from tests.test_worker_heartbeat_migration import verify_worker_heartbeat_migration

pytestmark = pytest.mark.integration


def test_worker_heartbeat_migration_preserves_data_on_postgres() -> None:
    url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    schema = f"heartbeat_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in url else "?"
    engine = create_engine(f"{url}{separator}options={quote_plus(f'-csearch_path={schema}')}")
    try:
        verify_worker_heartbeat_migration(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
