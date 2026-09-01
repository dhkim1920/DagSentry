"""Transactional Admin operations for Managed Connection metadata and secrets."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dagsentry.connection_config import validate_non_secret_config, validate_secret_config
from dagsentry.connection_crypto import ConnectionSecretCipher, EncryptedConnectionSecret
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import AdminAuditEventRecord, ManagedConnectionRecord

ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CONNECTION_VAULT_LOCK_ID = 1_143_776_053


class ConnectionManagementError(ValueError):
    """Base error for an invalid Managed Connection operation."""


class ConnectionNotFoundError(ConnectionManagementError):
    """No Managed Connection matched an Admin operation."""


class ConnectionConflictError(ConnectionManagementError):
    """A uniqueness or optimistic concurrency requirement was not met."""


class ConnectionRotationError(ConnectionManagementError):
    """Stored connection Secrets cannot be atomically moved to a new key."""


def put_connection(
    session: Session,
    *,
    connection_id: UUID,
    actor_user_id: UUID,
    environment: str,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
    display_name: str,
    non_secret_config: dict[str, object],
    secret_config: object | None,
    enabled: bool,
    expected_version: int | None,
    correlation_id: str | None,
    cipher: ConnectionSecretCipher | None,
    now: datetime | None = None,
) -> ManagedConnectionRecord:
    """Create or replace connection settings using optimistic concurrency."""
    normalized_environment = _normalize_environment(environment)
    normalized_display_name = _normalize_display_name(display_name)
    validated_config = validate_non_secret_config(purpose, provider, non_secret_config)
    validated_secret = (
        validate_secret_config(provider, secret_config) if secret_config is not None else None
    )
    changed_at = _as_utc(now or datetime.now(UTC))

    try:
        with session.begin():
            _lock_connection_vault(session)
            record = session.scalar(
                select(ManagedConnectionRecord)
                .where(ManagedConnectionRecord.id == connection_id)
                .with_for_update()
            )
            if record is None:
                if expected_version is not None:
                    raise ConnectionConflictError(
                        "new connection must not include expected_version"
                    )
                encrypted = _encrypt_secret(
                    validated_secret,
                    cipher=cipher,
                    connection_id=connection_id,
                    environment=normalized_environment,
                    purpose=purpose,
                    provider=provider,
                )
                record = ManagedConnectionRecord(
                    id=connection_id,
                    environment=normalized_environment,
                    purpose=purpose,
                    provider=provider,
                    display_name=normalized_display_name,
                    non_secret_config=validated_config,
                    secret_ciphertext=encrypted.ciphertext if encrypted is not None else None,
                    secret_nonce=encrypted.nonce if encrypted is not None else None,
                    secret_key_version=encrypted.key_version if encrypted is not None else None,
                    enabled=enabled,
                    version=1,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                    created_at=changed_at,
                    updated_at=changed_at,
                )
                session.add(record)
                session.flush()
                _add_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action="connection.created",
                    target_id=record.id,
                    change_summary={
                        "environment": record.environment,
                        "purpose": record.purpose.value,
                        "provider": record.provider.value,
                        "enabled": record.enabled,
                        "secret_configured": record.secret_ciphertext is not None,
                        "version": record.version,
                    },
                    correlation_id=correlation_id,
                    created_at=changed_at,
                )
                session.flush()
                return record

            if expected_version is None or record.version != expected_version:
                raise ConnectionConflictError("connection version does not match")
            aad_changes = (
                record.environment != normalized_environment
                or record.purpose != purpose
                or record.provider != provider
            )
            if aad_changes and record.secret_ciphertext is not None and validated_secret is None:
                raise ConnectionManagementError(
                    "changing environment, purpose, or provider requires a replacement Secret"
                )

            changed_fields = _changed_fields(
                record,
                environment=normalized_environment,
                purpose=purpose,
                provider=provider,
                display_name=normalized_display_name,
                non_secret_config=validated_config,
                enabled=enabled,
            )
            encrypted = _encrypt_secret(
                validated_secret,
                cipher=cipher,
                connection_id=record.id,
                environment=normalized_environment,
                purpose=purpose,
                provider=provider,
            )
            if not changed_fields and encrypted is None:
                return record

            record.environment = normalized_environment
            record.purpose = purpose
            record.provider = provider
            record.display_name = normalized_display_name
            record.non_secret_config = validated_config
            record.enabled = enabled
            if encrypted is not None:
                record.secret_ciphertext = encrypted.ciphertext
                record.secret_nonce = encrypted.nonce
                record.secret_key_version = encrypted.key_version
            record.version += 1
            record.updated_by_user_id = actor_user_id
            record.updated_at = changed_at
            if changed_fields:
                action = (
                    "connection.disabled"
                    if "enabled" in changed_fields and not enabled
                    else "connection.updated"
                )
                _add_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_id=record.id,
                    change_summary={"fields": sorted(changed_fields), "version": record.version},
                    correlation_id=correlation_id,
                    created_at=changed_at,
                )
            if encrypted is not None:
                _add_audit(
                    session,
                    actor_user_id=actor_user_id,
                    action="connection.secret_replaced",
                    target_id=record.id,
                    change_summary={"version": record.version},
                    correlation_id=correlation_id,
                    created_at=changed_at,
                )
            session.flush()
            return record
    except IntegrityError as error:
        raise ConnectionConflictError(
            "an environment already has a connection for this purpose"
        ) from error


def rotate_connection_secrets(
    session: Session,
    *,
    old_cipher: ConnectionSecretCipher,
    new_cipher: ConnectionSecretCipher,
    correlation_id: str,
    now: datetime | None = None,
) -> int:
    """Re-encrypt every configured Secret atomically under a strictly newer key version."""
    if new_cipher.key_version <= old_cipher.key_version:
        raise ConnectionRotationError("new key version must be greater than the current version")
    rotated_at = _as_utc(now or datetime.now(UTC))
    with session.begin():
        _lock_connection_vault(session)
        records = session.scalars(
            select(ManagedConnectionRecord)
            .where(ManagedConnectionRecord.secret_ciphertext.is_not(None))
            .order_by(ManagedConnectionRecord.id)
            .with_for_update()
        ).all()
        for record in records:
            if (
                record.secret_ciphertext is None
                or record.secret_nonce is None
                or record.secret_key_version is None
            ):
                raise ConnectionRotationError("stored connection Secret metadata is incomplete")
            if record.secret_key_version != old_cipher.key_version:
                raise ConnectionRotationError(
                    "stored connection Secret key version does not match the current version"
                )
            secret = old_cipher.decrypt(
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
            encrypted = new_cipher.encrypt(
                secret,
                connection_id=record.id,
                environment=record.environment,
                purpose=record.purpose,
                provider=record.provider,
            )
            record.secret_ciphertext = encrypted.ciphertext
            record.secret_nonce = encrypted.nonce
            record.secret_key_version = encrypted.key_version
            record.version += 1
            record.updated_by_user_id = None
            record.updated_at = rotated_at
            _add_audit(
                session,
                actor_user_id=None,
                action="connection.secret_rotated",
                target_id=record.id,
                change_summary={
                    "source": "cli",
                    "previous_key_version": old_cipher.key_version,
                    "key_version": new_cipher.key_version,
                    "version": record.version,
                },
                correlation_id=correlation_id,
                created_at=rotated_at,
            )
        session.flush()
        return len(records)


def disable_connection(
    session: Session,
    *,
    connection_id: UUID,
    actor_user_id: UUID,
    expected_version: int,
    correlation_id: str | None,
    now: datetime | None = None,
) -> ManagedConnectionRecord:
    """Disable a connection without deleting its encrypted settings or audit identity."""
    changed_at = _as_utc(now or datetime.now(UTC))
    with session.begin():
        record = session.scalar(
            select(ManagedConnectionRecord)
            .where(ManagedConnectionRecord.id == connection_id)
            .with_for_update()
        )
        if record is None:
            raise ConnectionNotFoundError("connection was not found")
        if record.version != expected_version:
            raise ConnectionConflictError("connection version does not match")
        if not record.enabled:
            return record
        record.enabled = False
        record.version += 1
        record.updated_by_user_id = actor_user_id
        record.updated_at = changed_at
        _add_audit(
            session,
            actor_user_id=actor_user_id,
            action="connection.disabled",
            target_id=record.id,
            change_summary={"version": record.version},
            correlation_id=correlation_id,
            created_at=changed_at,
        )
        session.flush()
    return record


def _encrypt_secret(
    secret: dict[str, str] | None,
    *,
    cipher: ConnectionSecretCipher | None,
    connection_id: UUID,
    environment: str,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
) -> EncryptedConnectionSecret | None:
    if secret is None:
        return None
    if cipher is None:
        raise ConnectionManagementError("connection encryption key is not configured")
    return cipher.encrypt(
        secret,
        connection_id=connection_id,
        environment=environment,
        purpose=purpose,
        provider=provider,
    )


def _changed_fields(
    record: ManagedConnectionRecord,
    *,
    environment: str,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
    display_name: str,
    non_secret_config: dict[str, object],
    enabled: bool,
) -> set[str]:
    values: dict[str, object] = {
        "environment": environment,
        "purpose": purpose,
        "provider": provider,
        "display_name": display_name,
        "non_secret_config": non_secret_config,
        "enabled": enabled,
    }
    return {field for field, value in values.items() if getattr(record, field) != value}


def _add_audit(
    session: Session,
    *,
    actor_user_id: UUID | None,
    action: str,
    target_id: UUID,
    change_summary: dict[str, object],
    correlation_id: str | None,
    created_at: datetime,
) -> None:
    session.add(
        AdminAuditEventRecord(
            actor_user_id=actor_user_id,
            action=action,
            target_type="connection",
            target_id=target_id,
            change_summary=change_summary,
            correlation_id=correlation_id,
            created_at=created_at,
        )
    )


def _lock_connection_vault(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": CONNECTION_VAULT_LOCK_ID},
        )


def _normalize_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if not ENVIRONMENT_PATTERN.fullmatch(normalized):
        raise ConnectionManagementError("environment is invalid")
    return normalized


def _normalize_display_name(display_name: str) -> str:
    normalized = display_name.strip()
    if not normalized or len(normalized) > 250:
        raise ConnectionManagementError("display name must contain between 1 and 250 characters")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
