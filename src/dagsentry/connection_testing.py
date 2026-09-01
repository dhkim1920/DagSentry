"""Read-only tests for supported Managed Connection Providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.config import Settings
from dagsentry.connection_crypto import (
    ConnectionEncryptionError,
    ConnectionSecretCipher,
    EncryptedConnectionSecret,
)
from dagsentry.connection_management import ConnectionConflictError, ConnectionNotFoundError
from dagsentry.domain.connection import (
    ConnectionProvider,
    ConnectionPurpose,
    ConnectionTestErrorCategory,
    ConnectionTestStatus,
)
from dagsentry.models import AdminAuditEventRecord, ManagedConnectionRecord

TESTABLE_PROVIDERS = frozenset(
    {
        ConnectionProvider.AIRFLOW,
        ConnectionProvider.OLLAMA,
        ConnectionProvider.SLACK,
    }
)


class UnsupportedConnectionTestError(ValueError):
    """A Provider test would cause side effects or potentially billable work."""


@dataclass(frozen=True)
class ConnectionTestSnapshot:
    """Immutable connection material used for one external test call."""

    id: UUID
    environment: str
    purpose: ConnectionPurpose
    provider: ConnectionProvider
    non_secret_config: dict[str, object]
    secret: dict[str, str]
    version: int


@dataclass(frozen=True)
class ConnectionTestOutcome:
    """Bounded test result that cannot contain a Provider response body."""

    status: ConnectionTestStatus
    error_category: ConnectionTestErrorCategory | None = None


def load_connection_test_snapshot(
    session: Session,
    *,
    connection_id: UUID,
    settings: Settings,
) -> ConnectionTestSnapshot:
    """Load and decrypt one enabled connection before making any external request."""
    record = session.get(ManagedConnectionRecord, connection_id)
    if record is None:
        raise ConnectionNotFoundError("connection was not found")
    if not record.enabled:
        raise ConnectionConflictError("disabled connection cannot be tested")
    if record.provider not in TESTABLE_PROVIDERS:
        raise UnsupportedConnectionTestError(
            f"read-only test is not supported for {record.provider.value}"
        )
    secret: dict[str, str] = {}
    if record.secret_ciphertext is not None:
        if record.secret_nonce is None or record.secret_key_version is None:
            raise ConnectionEncryptionError("connection Secret metadata is incomplete")
        secret = ConnectionSecretCipher.from_settings(settings).decrypt(
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
    return ConnectionTestSnapshot(
        id=record.id,
        environment=record.environment,
        purpose=record.purpose,
        provider=record.provider,
        non_secret_config=dict(record.non_secret_config),
        secret=secret,
        version=record.version,
    )


def test_connection(
    snapshot: ConnectionTestSnapshot,
    client: httpx.Client,
) -> ConnectionTestOutcome:
    """Execute one explicitly read-only Provider check without retries or redirects."""
    if snapshot.provider not in TESTABLE_PROVIDERS:
        raise UnsupportedConnectionTestError(
            f"read-only test is not supported for {snapshot.provider.value}"
        )
    try:
        if snapshot.provider == ConnectionProvider.AIRFLOW:
            return _test_airflow(snapshot, client)
        if snapshot.provider == ConnectionProvider.OLLAMA:
            return _test_ollama(snapshot, client)
        return _test_slack(snapshot, client)
    except httpx.TimeoutException:
        return _failed(ConnectionTestErrorCategory.TIMEOUT)
    except httpx.TransportError:
        return _failed(ConnectionTestErrorCategory.UNAVAILABLE)
    except (KeyError, TypeError, ValueError):
        return _failed(ConnectionTestErrorCategory.CONFIGURATION)


def record_connection_test(
    session: Session,
    *,
    snapshot: ConnectionTestSnapshot,
    outcome: ConnectionTestOutcome,
    actor_user_id: UUID,
    correlation_id: str | None,
    now: datetime | None = None,
) -> datetime:
    """Persist a test result only if the exact tested connection version is still current."""
    tested_at = _as_utc(now or datetime.now(UTC))
    with session.begin():
        record = session.scalar(
            select(ManagedConnectionRecord)
            .where(ManagedConnectionRecord.id == snapshot.id)
            .with_for_update()
        )
        if record is None:
            raise ConnectionNotFoundError("connection was not found")
        if record.version != snapshot.version or not record.enabled:
            raise ConnectionConflictError("connection changed while the test was running")
        record.last_tested_at = tested_at
        record.last_test_status = outcome.status
        record.last_test_error_category = (
            outcome.error_category.value if outcome.error_category is not None else None
        )
        session.add(
            AdminAuditEventRecord(
                actor_user_id=actor_user_id,
                action="connection.tested",
                target_type="connection",
                target_id=record.id,
                change_summary={
                    "status": outcome.status.value,
                    "error_category": (
                        outcome.error_category.value if outcome.error_category is not None else None
                    ),
                    "version": record.version,
                },
                correlation_id=correlation_id,
                created_at=tested_at,
            )
        )
        session.flush()
    return tested_at


def _test_airflow(
    snapshot: ConnectionTestSnapshot,
    client: httpx.Client,
) -> ConnectionTestOutcome:
    base_url = _required_string(snapshot.non_secret_config, "api_base_url")
    timeout = _timeout(snapshot.non_secret_config)
    headers = {"Accept": "application/json"}
    token = snapshot.secret.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.get(
        f"{base_url.rstrip('/')}/version",
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    )
    error = _http_error(response)
    if error is not None:
        return _failed(error)
    body = _json_object(response)
    if not isinstance(body.get("version"), str) or not body["version"]:
        return _failed(ConnectionTestErrorCategory.INVALID_RESPONSE)
    return ConnectionTestOutcome(ConnectionTestStatus.PASSED)


def _test_ollama(
    snapshot: ConnectionTestSnapshot,
    client: httpx.Client,
) -> ConnectionTestOutcome:
    base_url = _required_string(snapshot.non_secret_config, "api_base_url")
    response = client.get(
        f"{base_url.rstrip('/')}/tags",
        headers={"Accept": "application/json"},
        timeout=_timeout(snapshot.non_secret_config),
        follow_redirects=False,
    )
    error = _http_error(response)
    if error is not None:
        return _failed(error)
    if not isinstance(_json_object(response).get("models"), list):
        return _failed(ConnectionTestErrorCategory.INVALID_RESPONSE)
    return ConnectionTestOutcome(ConnectionTestStatus.PASSED)


def _test_slack(
    snapshot: ConnectionTestSnapshot,
    client: httpx.Client,
) -> ConnectionTestOutcome:
    base_url = _required_string(snapshot.non_secret_config, "api_base_url")
    channel = _required_string(snapshot.non_secret_config, "channel")
    token = _required_string(snapshot.secret, "bot_token")
    timeout = _timeout(snapshot.non_secret_config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    auth_response = client.post(
        f"{base_url.rstrip('/')}/auth.test",
        headers=headers,
        json={},
        timeout=timeout,
        follow_redirects=False,
    )
    error = _slack_error(auth_response)
    if error is not None:
        return _failed(error)
    channel_response = client.get(
        f"{base_url.rstrip('/')}/conversations.info",
        headers=headers,
        params={"channel": channel},
        timeout=timeout,
        follow_redirects=False,
    )
    error = _slack_error(channel_response)
    if error is not None:
        return _failed(error)
    if not isinstance(_json_object(channel_response).get("channel"), dict):
        return _failed(ConnectionTestErrorCategory.INVALID_RESPONSE)
    return ConnectionTestOutcome(ConnectionTestStatus.PASSED)


def _slack_error(response: httpx.Response) -> ConnectionTestErrorCategory | None:
    error = _http_error(response)
    if error is not None:
        return error
    body = _json_object(response)
    if body.get("ok") is True:
        return None
    code = body.get("error")
    if code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
        return ConnectionTestErrorCategory.AUTHENTICATION
    if code in {"missing_scope", "no_permission", "channel_not_found", "not_in_channel"}:
        return ConnectionTestErrorCategory.AUTHORIZATION
    if code == "ratelimited":
        return ConnectionTestErrorCategory.RATE_LIMITED
    return ConnectionTestErrorCategory.INVALID_RESPONSE


def _http_error(response: httpx.Response) -> ConnectionTestErrorCategory | None:
    if response.status_code == 401:
        return ConnectionTestErrorCategory.AUTHENTICATION
    if response.status_code == 403:
        return ConnectionTestErrorCategory.AUTHORIZATION
    if response.status_code == 429:
        return ConnectionTestErrorCategory.RATE_LIMITED
    if response.status_code == 408 or response.status_code >= 500:
        return ConnectionTestErrorCategory.UNAVAILABLE
    if response.status_code >= 400:
        return ConnectionTestErrorCategory.INVALID_REQUEST
    return None


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _required_string(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    return value


def _timeout(config: dict[str, object]) -> float:
    value = config.get("timeout_seconds")
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError("timeout_seconds is invalid")
    return float(value)


def _failed(category: ConnectionTestErrorCategory) -> ConnectionTestOutcome:
    return ConnectionTestOutcome(ConnectionTestStatus.FAILED, category)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
