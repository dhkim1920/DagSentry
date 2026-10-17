import os
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from tests.test_managed_connection_migration import verify_smtp_migration

pytestmark = pytest.mark.integration


def test_smtp_constraints_upgrade_preservation_and_downgrade_guard() -> None:
    url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    schema = f"managed_smtp_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in url else "?"
    engine = create_engine(f"{url}{separator}options={quote_plus(f'-csearch_path={schema}')}")
    try:
        verify_smtp_migration(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
