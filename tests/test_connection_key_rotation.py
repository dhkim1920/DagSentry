from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from dagsentry.connection_crypto import (
    ConnectionEncryptionError,
    ConnectionSecretCipher,
    EncryptedConnectionSecret,
)
from dagsentry.connection_management import ConnectionRotationError, rotate_connection_secrets
from dagsentry.db import SessionFactory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import AdminAuditEventRecord, ManagedConnectionRecord


def test_rotation_reencrypts_all_secrets_with_one_audited_key_version(
    session_factory: SessionFactory,
) -> None:
    old_cipher = ConnectionSecretCipher(b"a" * 32, 1)
    new_cipher = ConnectionSecretCipher(b"b" * 32, 2)
    records = [
        encrypted_record(
            connection_id=UUID(int=1),
            environment="production",
            purpose=ConnectionPurpose.AIRFLOW,
            provider=ConnectionProvider.AIRFLOW,
            secret={"token": "airflow-secret"},
            cipher=old_cipher,
        ),
        encrypted_record(
            connection_id=UUID(int=2),
            environment="production",
            purpose=ConnectionPurpose.NOTIFICATION,
            provider=ConnectionProvider.SLACK,
            secret={"bot_token": "slack-secret"},
            cipher=old_cipher,
        ),
    ]
    original_encrypted = {
        record.id: (record.secret_ciphertext, record.secret_nonce) for record in records
    }
    with session_factory() as session:
        session.add_all(records)
        session.commit()
    with session_factory() as session:
        rotated = rotate_connection_secrets(
            session,
            old_cipher=old_cipher,
            new_cipher=new_cipher,
            correlation_id="rotation-correlation-id",
        )

    assert rotated == 2
    with session_factory() as session:
        stored = session.scalars(
            select(ManagedConnectionRecord).order_by(ManagedConnectionRecord.id)
        ).all()
        assert [record.secret_key_version for record in stored] == [2, 2]
        assert [record.version for record in stored] == [2, 2]
        for record in stored:
            assert (record.secret_ciphertext, record.secret_nonce) != original_encrypted[record.id]
            assert record.updated_by_user_id is None
        assert decrypt_record(new_cipher, stored[0]) == {"token": "airflow-secret"}
        assert decrypt_record(new_cipher, stored[1]) == {"bot_token": "slack-secret"}
        events = session.scalars(
            select(AdminAuditEventRecord).order_by(AdminAuditEventRecord.target_id)
        ).all()
        assert len(events) == 2
        assert {event.action for event in events} == {"connection.secret_rotated"}
        assert {event.correlation_id for event in events} == {"rotation-correlation-id"}
        assert all(event.actor_user_id is None for event in events)
        assert all("secret" not in str(event.change_summary).lower() for event in events)


def test_rotation_rolls_back_every_record_when_one_ciphertext_is_invalid(
    session_factory: SessionFactory,
) -> None:
    old_cipher = ConnectionSecretCipher(b"a" * 32, 1)
    new_cipher = ConnectionSecretCipher(b"b" * 32, 2)
    first = encrypted_record(
        connection_id=UUID(int=1),
        environment="production",
        purpose=ConnectionPurpose.AIRFLOW,
        provider=ConnectionProvider.AIRFLOW,
        secret={"token": "airflow-secret"},
        cipher=old_cipher,
    )
    second = encrypted_record(
        connection_id=UUID(int=2),
        environment="production",
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
        secret={"bot_token": "slack-secret"},
        cipher=old_cipher,
    )
    assert second.secret_ciphertext is not None
    second.secret_ciphertext = second.secret_ciphertext[:-1] + bytes(
        [second.secret_ciphertext[-1] ^ 1]
    )
    originals = {
        first.id: (first.secret_ciphertext, first.secret_nonce),
        second.id: (second.secret_ciphertext, second.secret_nonce),
    }
    with session_factory() as session:
        session.add_all([first, second])
        session.commit()

    with session_factory() as session:
        with pytest.raises(ConnectionEncryptionError):
            rotate_connection_secrets(
                session,
                old_cipher=old_cipher,
                new_cipher=new_cipher,
                correlation_id="failed-rotation",
            )

    with session_factory() as session:
        stored = session.scalars(select(ManagedConnectionRecord)).all()
        assert all(record.secret_key_version == 1 for record in stored)
        assert all(record.version == 1 for record in stored)
        assert all(
            (record.secret_ciphertext, record.secret_nonce) == originals[record.id]
            for record in stored
        )
        assert session.scalar(select(AdminAuditEventRecord)) is None


def test_rotation_requires_strictly_newer_key_version(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        with pytest.raises(ConnectionRotationError, match="greater"):
            rotate_connection_secrets(
                session,
                old_cipher=ConnectionSecretCipher(b"a" * 32, 2),
                new_cipher=ConnectionSecretCipher(b"b" * 32, 2),
                correlation_id="invalid-rotation",
            )


def encrypted_record(
    *,
    connection_id: UUID,
    environment: str,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
    secret: dict[str, str],
    cipher: ConnectionSecretCipher,
) -> ManagedConnectionRecord:
    encrypted = cipher.encrypt(
        secret,
        connection_id=connection_id,
        environment=environment,
        purpose=purpose,
        provider=provider,
    )
    return ManagedConnectionRecord(
        id=connection_id,
        environment=environment,
        purpose=purpose,
        provider=provider,
        display_name=provider.value,
        non_secret_config={},
        secret_ciphertext=encrypted.ciphertext,
        secret_nonce=encrypted.nonce,
        secret_key_version=encrypted.key_version,
        enabled=True,
        version=1,
    )


def decrypt_record(
    cipher: ConnectionSecretCipher,
    record: ManagedConnectionRecord,
) -> dict[str, str]:
    assert record.secret_ciphertext is not None
    assert record.secret_nonce is not None
    assert record.secret_key_version is not None
    return cipher.decrypt(
        EncryptedConnectionSecret(
            record.secret_ciphertext,
            record.secret_nonce,
            record.secret_key_version,
        ),
        connection_id=record.id,
        environment=record.environment,
        purpose=record.purpose,
        provider=record.provider,
    )
