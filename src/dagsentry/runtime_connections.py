"""Immutable Managed Connection snapshots used by runtime jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from dagsentry.config import Settings
from dagsentry.connection_config import (
    AirflowConnectionConfig,
    AirflowSecretConfig,
    OllamaConnectionConfig,
    SlackConnectionConfig,
    SlackSecretConfig,
)
from dagsentry.connection_crypto import (
    ConnectionEncryptionError,
    ConnectionSecretCipher,
    EncryptedConnectionSecret,
)
from dagsentry.db import SessionFactory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.llm import LLMProvider
from dagsentry.models import ManagedConnectionRecord
from dagsentry.providers import (
    NotificationProviderAdapter,
    create_llm_provider,
    create_notification_provider,
)
from dagsentry.providers.ollama import OllamaProvider, OllamaProviderConfig
from dagsentry.providers.slack import SlackNotificationProvider, SlackProviderConfig
from dagsentry.task_logs import AirflowLogClient, AirflowLogClientConfig, TaskLogFetcher


class RuntimeConnectionError(ValueError):
    """A selected runtime connection source cannot provide a usable snapshot."""


@dataclass(frozen=True)
class AirflowRuntimeConnection:
    """Airflow settings fixed for the lifetime of one Worker job."""

    source: Literal["environment", "database"]
    log_fetcher: TaskLogFetcher
    ui_base_url: str | None
    connection_id: UUID | None = None
    version: int | None = None

    def __repr__(self) -> str:
        return (
            f"AirflowRuntimeConnection(source={self.source!r}, "
            f"connection_id={self.connection_id!r}, version={self.version!r})"
        )


class AirflowRuntimeConnectionResolver(Protocol):
    """Resolve the Airflow connection required by one failure event."""

    def resolve(self, environment: str) -> AirflowRuntimeConnection:
        """Return one immutable snapshot for an entire Worker job."""


@dataclass(frozen=True)
class LLMRuntimeConnection:
    """LLM Provider settings fixed for one diagnosis attempt."""

    source: Literal["environment", "database"]
    provider: LLMProvider | None
    connection_id: UUID | None = None
    version: int | None = None

    def __repr__(self) -> str:
        return (
            f"LLMRuntimeConnection(source={self.source!r}, "
            f"connection_id={self.connection_id!r}, version={self.version!r})"
        )


class LLMRuntimeConnectionResolver(Protocol):
    """Resolve the LLM connection required by one failure event."""

    def resolve(self, environment: str) -> LLMRuntimeConnection:
        """Return one immutable snapshot for a single diagnosis attempt."""


@dataclass(frozen=True)
class NotificationRuntimeConnection:
    """Notification Provider settings fixed for one runtime job or run."""

    source: Literal["environment", "database"]
    provider: NotificationProviderAdapter
    connection_id: UUID | None = None
    version: int | None = None

    def __repr__(self) -> str:
        return (
            f"NotificationRuntimeConnection(source={self.source!r}, "
            f"connection_id={self.connection_id!r}, version={self.version!r})"
        )


class NotificationRuntimeConnectionResolver(Protocol):
    """Resolve the Notification connection required by one environment."""

    def resolve(
        self, environment: str, *, connection_id: UUID | None = None
    ) -> NotificationRuntimeConnection:
        """Return one immutable snapshot for a single runtime job or run."""


class ConfiguredAirflowConnectionResolver:
    """Resolve Airflow settings from the explicitly selected source."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.http_client = http_client or httpx.Client(follow_redirects=False)

    def resolve(self, environment: str) -> AirflowRuntimeConnection:
        """Load the latest enabled Airflow settings without cross-source fallback."""
        if self.settings.airflow_config_source == "environment":
            return self._from_environment()
        return self._from_database(environment)

    def _from_environment(self) -> AirflowRuntimeConnection:
        if self.settings.airflow_api_base_url is None or self.settings.airflow_api_token is None:
            raise RuntimeConnectionError("Airflow environment configuration is unavailable")
        try:
            config = AirflowLogClientConfig(
                base_url=self.settings.airflow_api_base_url,
                api_token=self.settings.airflow_api_token.get_secret_value(),
                timeout_seconds=self.settings.airflow_api_timeout_seconds,
                max_attempts=self.settings.airflow_api_max_attempts,
                retry_backoff_seconds=self.settings.airflow_api_retry_backoff_seconds,
                max_response_bytes=self.settings.airflow_log_max_response_bytes,
            )
        except ValueError as error:
            raise RuntimeConnectionError("Airflow environment configuration is invalid") from error
        return AirflowRuntimeConnection(
            source="environment",
            log_fetcher=AirflowLogClient(config, self.http_client),
            ui_base_url=self.settings.airflow_ui_base_url,
        )

    def _from_database(self, environment: str) -> AirflowRuntimeConnection:
        try:
            with self.session_factory() as session:
                record = session.scalar(
                    select(ManagedConnectionRecord).where(
                        ManagedConnectionRecord.environment == environment,
                        ManagedConnectionRecord.purpose == ConnectionPurpose.AIRFLOW,
                        ManagedConnectionRecord.provider == ConnectionProvider.AIRFLOW,
                        ManagedConnectionRecord.enabled.is_(True),
                    )
                )
                if record is None:
                    raise RuntimeConnectionError("Airflow database configuration is unavailable")
                snapshot = _database_airflow_snapshot(record, self.settings)
        except RuntimeConnectionError:
            raise
        except (ConnectionEncryptionError, SQLAlchemyError, ValidationError, ValueError) as error:
            raise RuntimeConnectionError("Airflow database configuration is unavailable") from error

        return AirflowRuntimeConnection(
            source="database",
            log_fetcher=AirflowLogClient(snapshot.config, self.http_client),
            ui_base_url=snapshot.ui_base_url,
            connection_id=snapshot.connection_id,
            version=snapshot.version,
        )


class ConfiguredLLMConnectionResolver:
    """Resolve LLM settings from the explicitly selected source."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        ollama_http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.ollama_http_client = ollama_http_client or httpx.Client(follow_redirects=False)
        self.environment_provider = (
            create_llm_provider(settings) if settings.llm_config_source == "environment" else None
        )

    def resolve(self, environment: str) -> LLMRuntimeConnection:
        """Load the latest enabled LLM settings without cross-source fallback."""
        if self.settings.llm_config_source == "environment":
            return LLMRuntimeConnection(
                source="environment",
                provider=self.environment_provider,
            )
        return self._from_database(environment)

    def _from_database(self, environment: str) -> LLMRuntimeConnection:
        try:
            with self.session_factory() as session:
                record = session.scalar(
                    select(ManagedConnectionRecord).where(
                        ManagedConnectionRecord.environment == environment,
                        ManagedConnectionRecord.purpose == ConnectionPurpose.LLM,
                        ManagedConnectionRecord.enabled.is_(True),
                    )
                )
                if record is None or record.provider != ConnectionProvider.OLLAMA:
                    raise RuntimeConnectionError("Ollama database configuration is unavailable")
                config = OllamaConnectionConfig.model_validate(record.non_secret_config)
                connection_id = record.id
                version = record.version
        except RuntimeConnectionError:
            raise
        except (SQLAlchemyError, ValidationError, ValueError) as error:
            raise RuntimeConnectionError("Ollama database configuration is unavailable") from error

        return LLMRuntimeConnection(
            source="database",
            provider=OllamaProvider(
                OllamaProviderConfig(
                    model=config.model,
                    prompt_version=self.settings.llm_prompt_version,
                    base_url=str(config.api_base_url),
                    max_output_tokens=config.max_output_tokens,
                    timeout_seconds=config.timeout_seconds,
                    max_attempts=config.max_attempts,
                    retry_backoff_seconds=config.retry_backoff_seconds,
                ),
                self.ollama_http_client,
            ),
            connection_id=connection_id,
            version=version,
        )


class ConfiguredNotificationConnectionResolver:
    """Resolve Notification settings from the explicitly selected source."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        slack_http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.slack_http_client = slack_http_client or httpx.Client(follow_redirects=False)
        self.environment_provider = (
            create_notification_provider(settings)
            if settings.notification_config_source == "environment"
            else None
        )

    def resolve(
        self, environment: str, *, connection_id: UUID | None = None
    ) -> NotificationRuntimeConnection:
        """Load the latest enabled Notification settings without source fallback."""
        if self.settings.notification_config_source == "environment":
            assert self.environment_provider is not None
            return NotificationRuntimeConnection(
                source="environment",
                provider=self.environment_provider,
            )
        return self._from_database(environment, connection_id=connection_id)

    def _from_database(
        self, environment: str, *, connection_id: UUID | None = None
    ) -> NotificationRuntimeConnection:
        try:
            with self.session_factory() as session:
                filters = [
                    ManagedConnectionRecord.environment == environment,
                    ManagedConnectionRecord.purpose == ConnectionPurpose.NOTIFICATION,
                    ManagedConnectionRecord.enabled.is_(True),
                ]
                if connection_id is not None:
                    filters.append(ManagedConnectionRecord.id == connection_id)
                record = session.scalar(select(ManagedConnectionRecord).where(*filters))
                if record is None or record.provider != ConnectionProvider.SLACK:
                    raise RuntimeConnectionError("Slack database configuration is unavailable")
                config, secret = _database_slack_config(record, self.settings)
                connection_id = record.id
                version = record.version
        except RuntimeConnectionError:
            raise
        except (ConnectionEncryptionError, SQLAlchemyError, ValidationError, ValueError) as error:
            raise RuntimeConnectionError("Slack database configuration is unavailable") from error

        return NotificationRuntimeConnection(
            source="database",
            provider=SlackNotificationProvider(
                SlackProviderConfig(
                    bot_token=secret.bot_token.get_secret_value(),
                    channel=config.channel,
                    base_url=str(config.api_base_url),
                    timeout_seconds=config.timeout_seconds,
                    max_attempts=config.max_attempts,
                    retry_backoff_seconds=config.retry_backoff_seconds,
                    trusted_http_hosts=config.trusted_http_hosts,
                ),
                self.slack_http_client,
            ),
            connection_id=connection_id,
            version=version,
        )


@dataclass(frozen=True)
class _DatabaseAirflowSnapshot:
    connection_id: UUID
    version: int
    config: AirflowLogClientConfig
    ui_base_url: str | None


def _database_airflow_snapshot(
    record: ManagedConnectionRecord,
    settings: Settings,
) -> _DatabaseAirflowSnapshot:
    if (
        record.secret_ciphertext is None
        or record.secret_nonce is None
        or record.secret_key_version is None
    ):
        raise RuntimeConnectionError("Airflow database configuration is unavailable")
    non_secret = AirflowConnectionConfig.model_validate(record.non_secret_config)
    decrypted = ConnectionSecretCipher.from_settings(settings).decrypt(
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
    secret = AirflowSecretConfig.model_validate(decrypted)
    return _DatabaseAirflowSnapshot(
        connection_id=record.id,
        version=record.version,
        config=AirflowLogClientConfig(
            base_url=str(non_secret.api_base_url),
            api_token=secret.token.get_secret_value(),
            timeout_seconds=non_secret.timeout_seconds,
            max_attempts=non_secret.max_attempts,
            retry_backoff_seconds=non_secret.retry_backoff_seconds,
            max_response_bytes=non_secret.log_max_response_bytes,
        ),
        ui_base_url=(str(non_secret.ui_base_url) if non_secret.ui_base_url is not None else None),
    )


def _database_slack_config(
    record: ManagedConnectionRecord,
    settings: Settings,
) -> tuple[SlackConnectionConfig, SlackSecretConfig]:
    if (
        record.secret_ciphertext is None
        or record.secret_nonce is None
        or record.secret_key_version is None
    ):
        raise RuntimeConnectionError("Slack database configuration is unavailable")
    config = SlackConnectionConfig.model_validate(record.non_secret_config)
    decrypted = ConnectionSecretCipher.from_settings(settings).decrypt(
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
    return config, SlackSecretConfig.model_validate(decrypted)
