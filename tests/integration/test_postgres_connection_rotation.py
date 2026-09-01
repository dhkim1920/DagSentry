from __future__ import annotations

import os
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from dagsentry.config import get_settings
from dagsentry.connection_crypto import (
    ConnectionSecretCipher,
    EncryptedConnectionSecret,
)
from dagsentry.connection_management import rotate_connection_secrets
from dagsentry.db import create_session_factory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import ManagedConnectionRecord

pytestmark = pytest.mark.integration


def test_postgres_rotates_bytea_secrets_under_the_vault_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    schema = f"connection_rotation_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    schema_url = f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    original_url = os.environ.get("DAGSENTRY_DATABASE_URL")
    monkeypatch.setenv("DAGSENTRY_DATABASE_URL", schema_url)
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        session_factory = create_session_factory(schema_url)
        old_cipher = ConnectionSecretCipher(b"a" * 32, 1)
        new_cipher = ConnectionSecretCipher(b"b" * 32, 2)
        connection_id = uuid4()
        encrypted = old_cipher.encrypt(
            {"bot_token": "postgres-slack-secret"},
            connection_id=connection_id,
            environment="production",
            purpose=ConnectionPurpose.NOTIFICATION,
            provider=ConnectionProvider.SLACK,
        )
        with session_factory() as session:
            session.add(
                ManagedConnectionRecord(
                    id=connection_id,
                    environment="production",
                    purpose=ConnectionPurpose.NOTIFICATION,
                    provider=ConnectionProvider.SLACK,
                    display_name="Production Slack",
                    non_secret_config={"channel": "C0123456789"},
                    secret_ciphertext=encrypted.ciphertext,
                    secret_nonce=encrypted.nonce,
                    secret_key_version=encrypted.key_version,
                    enabled=True,
                    version=1,
                )
            )
            session.commit()
        with session_factory() as session:
            assert (
                rotate_connection_secrets(
                    session,
                    old_cipher=old_cipher,
                    new_cipher=new_cipher,
                    correlation_id="postgres-rotation",
                )
                == 1
            )
        with session_factory() as session:
            record = session.get(ManagedConnectionRecord, connection_id)
            assert record is not None
            assert record.secret_ciphertext is not None
            assert record.secret_nonce is not None
            assert record.secret_key_version == 2
            assert record.version == 2
            assert new_cipher.decrypt(
                EncryptedConnectionSecret(
                    record.secret_ciphertext,
                    record.secret_nonce,
                    record.secret_key_version,
                ),
                connection_id=record.id,
                environment=record.environment,
                purpose=record.purpose,
                provider=record.provider,
            ) == {"bot_token": "postgres-slack-secret"}
        command.downgrade(alembic_config, "base")
    finally:
        if original_url is None:
            monkeypatch.delenv("DAGSENTRY_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DAGSENTRY_DATABASE_URL", original_url)
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
