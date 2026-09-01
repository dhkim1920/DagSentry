from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.connection_crypto import ConnectionSecretCipher
from dagsentry.db import SessionFactory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import ManagedConnectionRecord
from dagsentry.providers.ollama import OllamaProvider
from dagsentry.providers.slack import SlackNotificationProvider
from dagsentry.runtime_connections import (
    ConfiguredAirflowConnectionResolver,
    ConfiguredLLMConnectionResolver,
    ConfiguredNotificationConnectionResolver,
    RuntimeConnectionError,
)
from dagsentry.task_logs import AirflowLogClient, LogCollectionStatus, TaskLogReference
from tests.contracts.notification import notification_payload

KEY = base64.b64encode(bytes(range(32))).decode()


def reference() -> TaskLogReference:
    return TaskLogReference(
        dag_id="orders",
        dag_run_id="run-1",
        task_id="load",
        map_index=-1,
        try_number=1,
    )


def test_environment_source_preserves_existing_airflow_settings(
    session_factory: SessionFactory,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"content": [{"event": "log"}], "continuation_token": None},
        )

    settings = Settings(
        airflow_config_source="environment",
        airflow_api_base_url="http://environment-airflow:8080",
        airflow_api_token=SecretStr("environment-token"),
        airflow_ui_base_url="http://environment-airflow-ui:8080",
    )
    resolver = ConfiguredAirflowConnectionResolver(
        settings,
        session_factory,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    snapshot = resolver.resolve("test")
    result = snapshot.log_fetcher.fetch(reference())

    assert snapshot.source == "environment"
    assert snapshot.connection_id is None
    assert snapshot.version is None
    assert snapshot.ui_base_url == "http://environment-airflow-ui:8080"
    assert result.status == LogCollectionStatus.AVAILABLE
    assert requests[0].url.host == "environment-airflow"
    assert requests[0].headers["Authorization"] == "Bearer environment-token"


def test_database_source_uses_matching_enabled_connection_without_environment_fallback(
    session_factory: SessionFactory,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"content": [{"event": "log"}], "continuation_token": None},
        )

    settings = database_settings()
    connection_id = add_airflow_connection(
        session_factory,
        settings,
        environment="test",
        api_base_url="http://database-airflow:8080",
        ui_base_url="http://database-airflow-ui:8080",
        token="database-token",
    )
    resolver = ConfiguredAirflowConnectionResolver(
        settings,
        session_factory,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    snapshot = resolver.resolve("test")
    result = snapshot.log_fetcher.fetch(reference())

    assert snapshot.source == "database"
    assert snapshot.connection_id == connection_id
    assert snapshot.version == 1
    assert snapshot.ui_base_url == "http://database-airflow-ui:8080/"
    assert result.status == LogCollectionStatus.AVAILABLE
    assert requests[0].url.host == "database-airflow"
    assert requests[0].headers["Authorization"] == "Bearer database-token"


def test_database_source_fails_closed_when_matching_connection_is_missing(
    session_factory: SessionFactory,
) -> None:
    resolver = ConfiguredAirflowConnectionResolver(database_settings(), session_factory)

    with pytest.raises(
        RuntimeConnectionError,
        match="Airflow database configuration is unavailable",
    ):
        resolver.resolve("test")


def test_database_source_reads_the_latest_version_for_each_snapshot(
    session_factory: SessionFactory,
) -> None:
    settings = database_settings()
    connection_id = add_airflow_connection(
        session_factory,
        settings,
        environment="test",
        api_base_url="http://airflow-v1:8080",
        ui_base_url="http://airflow-ui-v1:8080",
        token="token-v1",
    )
    resolver = ConfiguredAirflowConnectionResolver(settings, session_factory)

    first = resolver.resolve("test")
    replace_airflow_connection(
        session_factory,
        settings,
        connection_id=connection_id,
        api_base_url="http://airflow-v2:8080",
        ui_base_url="http://airflow-ui-v2:8080",
        token="token-v2",
    )
    second = resolver.resolve("test")

    assert first.version == 1
    assert first.ui_base_url == "http://airflow-ui-v1:8080/"
    assert second.version == 2
    assert second.ui_base_url == "http://airflow-ui-v2:8080/"
    assert isinstance(first.log_fetcher, AirflowLogClient)
    assert isinstance(second.log_fetcher, AirflowLogClient)
    assert first.log_fetcher.config.base_url == "http://airflow-v1:8080/"
    assert second.log_fetcher.config.base_url == "http://airflow-v2:8080/"


def test_environment_source_preserves_existing_ollama_settings(
    session_factory: SessionFactory,
) -> None:
    resolver = ConfiguredLLMConnectionResolver(
        Settings(
            llm_config_source="environment",
            llm_provider="ollama",
            llm_model="environment-model",
            ollama_api_base_url="http://environment-ollama:11434/api",
            ollama_max_output_tokens=1_500,
        ),
        session_factory,
    )

    snapshot = resolver.resolve("test")

    assert snapshot.source == "environment"
    assert snapshot.connection_id is None
    assert snapshot.version is None
    assert isinstance(snapshot.provider, OllamaProvider)
    assert snapshot.provider.config.model == "environment-model"
    assert snapshot.provider.config.base_url == "http://environment-ollama:11434/api"
    assert snapshot.provider.config.max_output_tokens == 1_500


def test_database_source_uses_ollama_connection_without_environment_fallback(
    session_factory: SessionFactory,
) -> None:
    settings = ollama_database_settings()
    connection_id = add_ollama_connection(
        session_factory,
        environment="test",
        api_base_url="http://database-ollama:11434/api",
        model="database-model",
        max_output_tokens=1_024,
    )
    resolver = ConfiguredLLMConnectionResolver(settings, session_factory)

    snapshot = resolver.resolve("test")

    assert snapshot.source == "database"
    assert snapshot.connection_id == connection_id
    assert snapshot.version == 1
    assert isinstance(snapshot.provider, OllamaProvider)
    assert snapshot.provider.config.model == "database-model"
    assert snapshot.provider.config.base_url == "http://database-ollama:11434/api"
    assert snapshot.provider.config.max_output_tokens == 1_024


def test_ollama_database_source_fails_closed_when_connection_is_missing(
    session_factory: SessionFactory,
) -> None:
    resolver = ConfiguredLLMConnectionResolver(ollama_database_settings(), session_factory)

    with pytest.raises(
        RuntimeConnectionError,
        match="Ollama database configuration is unavailable",
    ):
        resolver.resolve("test")


def test_ollama_database_source_reads_latest_version_for_each_snapshot(
    session_factory: SessionFactory,
) -> None:
    settings = ollama_database_settings()
    connection_id = add_ollama_connection(
        session_factory,
        environment="test",
        api_base_url="http://ollama-v1:11434/api",
        model="model-v1",
        max_output_tokens=1_024,
    )
    resolver = ConfiguredLLMConnectionResolver(settings, session_factory)

    first = resolver.resolve("test")
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        record.non_secret_config = {
            "api_base_url": "http://ollama-v2:11434/api",
            "model": "model-v2",
            "max_output_tokens": 2_048,
        }
        record.version = 2
    second = resolver.resolve("test")

    assert first.version == 1
    assert second.version == 2
    assert isinstance(first.provider, OllamaProvider)
    assert isinstance(second.provider, OllamaProvider)
    assert first.provider.config.model == "model-v1"
    assert second.provider.config.model == "model-v2"
    assert first.provider.config.base_url == "http://ollama-v1:11434/api"
    assert second.provider.config.base_url == "http://ollama-v2:11434/api"


def test_environment_source_preserves_existing_slack_settings(
    session_factory: SessionFactory,
) -> None:
    resolver = ConfiguredNotificationConnectionResolver(
        Settings(
            notification_config_source="environment",
            notification_provider="slack",
            slack_bot_token=SecretStr("environment-token"),
            slack_channel="C-ENVIRONMENT",
            slack_api_base_url="http://environment-slack/api",
            insecure_http_trusted_hosts=frozenset({"environment-slack"}),
        ),
        session_factory,
    )

    snapshot = resolver.resolve("test")

    assert snapshot.source == "environment"
    assert snapshot.connection_id is None
    assert snapshot.version is None
    assert isinstance(snapshot.provider, SlackNotificationProvider)
    assert snapshot.provider.config.channel == "C-ENVIRONMENT"
    assert snapshot.provider.config.base_url == "http://environment-slack/api"


def test_database_source_sends_slack_notification_without_environment_fallback(
    session_factory: SessionFactory,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    settings = slack_database_settings()
    connection_id = add_slack_connection(
        session_factory,
        settings,
        environment="test",
        api_base_url="http://database-slack/api",
        channel="C-DATABASE",
        token="database-token",
    )
    resolver = ConfiguredNotificationConnectionResolver(
        settings,
        session_factory,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    snapshot = resolver.resolve("test")
    status = snapshot.provider.send(notification_payload(), delivery_key="database-delivery")

    assert snapshot.source == "database"
    assert snapshot.connection_id == connection_id
    assert snapshot.version == 1
    assert status == 200
    assert requests[0].url == "http://database-slack/api/chat.postMessage"
    assert requests[0].headers["Authorization"] == "Bearer database-token"
    body = json.loads(requests[0].content)
    assert body["channel"] == "C-DATABASE"
    assert body["metadata"]["event_payload"]["delivery_key"] == "database-delivery"


def test_slack_database_source_fails_closed_when_connection_is_missing(
    session_factory: SessionFactory,
) -> None:
    resolver = ConfiguredNotificationConnectionResolver(
        slack_database_settings(),
        session_factory,
    )

    with pytest.raises(
        RuntimeConnectionError,
        match="Slack database configuration is unavailable",
    ):
        resolver.resolve("test")


def test_slack_database_source_reads_latest_version_for_each_snapshot(
    session_factory: SessionFactory,
) -> None:
    settings = slack_database_settings()
    connection_id = add_slack_connection(
        session_factory,
        settings,
        environment="test",
        api_base_url="http://slack-v1/api",
        channel="C-V1",
        token="token-v1",
    )
    resolver = ConfiguredNotificationConnectionResolver(settings, session_factory)

    first = resolver.resolve("test")
    replace_slack_connection(
        session_factory,
        settings,
        connection_id=connection_id,
        api_base_url="http://slack-v2/api",
        channel="C-V2",
        token="token-v2",
    )
    second = resolver.resolve("test")

    assert first.version == 1
    assert second.version == 2
    assert isinstance(first.provider, SlackNotificationProvider)
    assert isinstance(second.provider, SlackNotificationProvider)
    assert first.provider.config.channel == "C-V1"
    assert second.provider.config.channel == "C-V2"
    assert first.provider.config.base_url == "http://slack-v1/api"
    assert second.provider.config.base_url == "http://slack-v2/api"


def database_settings() -> Settings:
    return Settings(
        airflow_config_source="database",
        airflow_api_base_url="http://environment-must-not-be-used:8080",
        airflow_api_token=SecretStr("environment-token-must-not-be-used"),
        connection_encryption_key=SecretStr(KEY),
        connection_encryption_key_version=1,
    )


def ollama_database_settings() -> Settings:
    return Settings(
        llm_config_source="database",
        llm_provider="ollama",
        llm_model="environment-model-must-not-be-used",
        ollama_api_base_url="http://environment-must-not-be-used:11434/api",
    )


def slack_database_settings() -> Settings:
    return Settings(
        notification_config_source="database",
        notification_provider="slack",
        slack_bot_token=SecretStr("environment-token-must-not-be-used"),
        slack_channel="C-ENVIRONMENT-MUST-NOT-BE-USED",
        slack_api_base_url="http://environment-must-not-be-used/api",
        connection_encryption_key=SecretStr(KEY),
        connection_encryption_key_version=1,
    )


def add_ollama_connection(
    session_factory: SessionFactory,
    *,
    environment: str,
    api_base_url: str,
    model: str,
    max_output_tokens: int,
) -> UUID:
    connection_id = uuid4()
    with session_factory() as session, session.begin():
        session.add(
            ManagedConnectionRecord(
                id=connection_id,
                environment=environment,
                purpose=ConnectionPurpose.LLM,
                provider=ConnectionProvider.OLLAMA,
                display_name="Ollama",
                non_secret_config={
                    "api_base_url": api_base_url,
                    "model": model,
                    "max_output_tokens": max_output_tokens,
                },
                enabled=True,
                version=1,
            )
        )
    return connection_id


def add_slack_connection(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    environment: str,
    api_base_url: str,
    channel: str,
    token: str,
) -> UUID:
    connection_id = uuid4()
    encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
        {"bot_token": token},
        connection_id=connection_id,
        environment=environment,
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
    )
    with session_factory() as session, session.begin():
        session.add(
            ManagedConnectionRecord(
                id=connection_id,
                environment=environment,
                purpose=ConnectionPurpose.NOTIFICATION,
                provider=ConnectionProvider.SLACK,
                display_name="Slack",
                non_secret_config={
                    "api_base_url": api_base_url,
                    "channel": channel,
                    "trusted_http_hosts": [urlsplit(api_base_url).hostname],
                },
                secret_ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.nonce,
                secret_key_version=encrypted.key_version,
                enabled=True,
                version=1,
            )
        )
    return connection_id


def replace_slack_connection(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    connection_id: UUID,
    api_base_url: str,
    channel: str,
    token: str,
) -> None:
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
            {"bot_token": token},
            connection_id=record.id,
            environment=record.environment,
            purpose=record.purpose,
            provider=record.provider,
        )
        record.non_secret_config = {
            "api_base_url": api_base_url,
            "channel": channel,
            "trusted_http_hosts": [urlsplit(api_base_url).hostname],
        }
        record.secret_ciphertext = encrypted.ciphertext
        record.secret_nonce = encrypted.nonce
        record.secret_key_version = encrypted.key_version
        record.version = 2


def add_airflow_connection(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    environment: str,
    api_base_url: str,
    ui_base_url: str,
    token: str,
) -> UUID:
    connection_id = uuid4()
    encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
        {"token": token},
        connection_id=connection_id,
        environment=environment,
        purpose=ConnectionPurpose.AIRFLOW,
        provider=ConnectionProvider.AIRFLOW,
    )
    with session_factory() as session, session.begin():
        session.add(
            ManagedConnectionRecord(
                id=connection_id,
                environment=environment,
                purpose=ConnectionPurpose.AIRFLOW,
                provider=ConnectionProvider.AIRFLOW,
                display_name="Airflow",
                non_secret_config={
                    "api_base_url": api_base_url,
                    "ui_base_url": ui_base_url,
                },
                secret_ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.nonce,
                secret_key_version=encrypted.key_version,
                enabled=True,
                version=1,
            )
        )
    return connection_id


def replace_airflow_connection(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    connection_id: UUID,
    api_base_url: str,
    ui_base_url: str,
    token: str,
) -> None:
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
            {"token": token},
            connection_id=record.id,
            environment=record.environment,
            purpose=record.purpose,
            provider=record.provider,
        )
        record.non_secret_config = {
            "api_base_url": api_base_url,
            "ui_base_url": ui_base_url,
        }
        record.secret_ciphertext = encrypted.ciphertext
        record.secret_nonce = encrypted.nonce
        record.secret_key_version = encrypted.key_version
        record.version = 2
