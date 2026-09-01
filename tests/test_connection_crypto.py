from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.connection_crypto import (
    ConnectionEncryptionError,
    ConnectionSecretCipher,
    EncryptedConnectionSecret,
)
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose


def test_aes_gcm_round_trip_uses_fresh_nonce_and_bound_record_identity() -> None:
    cipher = ConnectionSecretCipher(bytes(range(32)), 3)
    connection_id = uuid4()
    secret = {"bot_token": "xoxb-do-not-store-in-plaintext"}

    first = cipher.encrypt(
        secret,
        connection_id=connection_id,
        environment="production",
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
    )
    second = cipher.encrypt(
        secret,
        connection_id=connection_id,
        environment="production",
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
    )

    assert first.key_version == 3
    assert len(first.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert secret["bot_token"].encode() not in first.ciphertext
    assert (
        cipher.decrypt(
            first,
            connection_id=connection_id,
            environment="production",
            purpose=ConnectionPurpose.NOTIFICATION,
            provider=ConnectionProvider.SLACK,
        )
        == secret
    )
    with pytest.raises(ConnectionEncryptionError, match="could not be decrypted"):
        cipher.decrypt(
            first,
            connection_id=connection_id,
            environment="staging",
            purpose=ConnectionPurpose.NOTIFICATION,
            provider=ConnectionProvider.SLACK,
        )


def test_cipher_fails_closed_for_tampering_wrong_key_and_key_version() -> None:
    cipher = ConnectionSecretCipher(b"a" * 32, 1)
    connection_id = uuid4()
    encrypted = cipher.encrypt(
        {"token": "airflow-secret"},
        connection_id=connection_id,
        environment="production",
        purpose=ConnectionPurpose.AIRFLOW,
        provider=ConnectionProvider.AIRFLOW,
    )
    tampered = EncryptedConnectionSecret(
        encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
        encrypted.nonce,
        encrypted.key_version,
    )

    with pytest.raises(ConnectionEncryptionError):
        cipher.decrypt(
            tampered,
            connection_id=connection_id,
            environment="production",
            purpose=ConnectionPurpose.AIRFLOW,
            provider=ConnectionProvider.AIRFLOW,
        )
    with pytest.raises(ConnectionEncryptionError):
        ConnectionSecretCipher(b"b" * 32, 1).decrypt(
            encrypted,
            connection_id=connection_id,
            environment="production",
            purpose=ConnectionPurpose.AIRFLOW,
            provider=ConnectionProvider.AIRFLOW,
        )
    with pytest.raises(ConnectionEncryptionError, match="version is unavailable"):
        ConnectionSecretCipher(b"a" * 32, 2).decrypt(
            encrypted,
            connection_id=connection_id,
            environment="production",
            purpose=ConnectionPurpose.AIRFLOW,
            provider=ConnectionProvider.AIRFLOW,
        )


def test_cipher_settings_require_base64_encoded_32_byte_key() -> None:
    encoded_key = base64.b64encode(b"k" * 32).decode()

    cipher = ConnectionSecretCipher.from_settings(
        Settings(
            database_url="sqlite+pysqlite://",
            connection_encryption_key=SecretStr(encoded_key),
            connection_encryption_key_version=4,
        )
    )

    assert cipher.key_version == 4
    with pytest.raises(ConnectionEncryptionError, match="not configured"):
        ConnectionSecretCipher.from_settings(
            Settings(
                database_url="sqlite+pysqlite://",
                connection_encryption_key=None,
            )
        )
    with pytest.raises(ConnectionEncryptionError, match="invalid"):
        ConnectionSecretCipher.from_settings(
            Settings(
                database_url="sqlite+pysqlite://",
                connection_encryption_key=SecretStr("not-base64"),
            )
        )
    with pytest.raises(ConnectionEncryptionError, match="32 bytes"):
        ConnectionSecretCipher.from_settings(
            Settings(
                database_url="sqlite+pysqlite://",
                connection_encryption_key=SecretStr(base64.b64encode(b"short").decode()),
            )
        )
