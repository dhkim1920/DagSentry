from __future__ import annotations

import base64
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.connection_config import validate_non_secret_config, validate_secret_config
from dagsentry.connection_crypto import ConnectionSecretCipher
from dagsentry.db import SessionFactory
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import ManagedConnectionRecord
from dagsentry.runtime_connections import (
    ConfiguredLLMConnectionResolver,
    ConfiguredNotificationConnectionResolver,
    RuntimeConnectionError,
)
from tests.contracts.llm import diagnosis_request
from tests.contracts.notification import notification_payload
from tests.test_openai_provider import response_body

CASES: dict[str, tuple[dict[str, object], dict[str, str]]] = {
    "OPENAI": ({"model": "database-model"}, {"api_key": "database-api-secret"}),
    "AZURE_OPENAI": (
        {"endpoint": "https://database.azure.example", "model": "database-model"},
        {"api_key": "database-api-secret"},
    ),
    "ANTHROPIC": ({"model": "database-model"}, {"api_key": "database-api-secret"}),
    "BEDROCK": (
        {"region": "ap-northeast-2", "model_id": "database-model"},
        {"access_key_id": "database-access-secret", "secret_access_key": "database-key-secret"},
    ),
    "WEBHOOK": ({}, {"url": "https://database.example/hook?key=database-url-secret"}),
    "TEAMS": ({}, {"webhook_url": "https://database.example/hook?key=database-url-secret"}),
    "DISCORD": ({}, {"webhook_url": "https://database.example/hook?key=database-url-secret"}),
    "SMTP": (
        {
            "host": "database.example",
            "sender": "sender@example.com",
            "recipients": ["to@example.com"],
        },
        {"username": "database-user-secret", "password": "database-password-secret"},
    ),
}


def database_settings() -> Settings:
    return Settings(
        llm_config_source="database",
        notification_config_source="database",
        connection_encryption_key=SecretStr(base64.b64encode(bytes(range(32))).decode()),
        openai_api_key=SecretStr("environment-secret-must-not-be-used"),
        smtp_password=SecretStr("environment-secret-must-not-be-used"),
    )


def add_connection(
    session_factory: SessionFactory,
    settings: Settings,
    name: str,
    *,
    secret_override: dict[str, str] | None = None,
) -> UUID:
    config, secret = CASES[name]
    provider = ConnectionProvider(name)
    purpose = (
        ConnectionPurpose.LLM
        if name in {"OPENAI", "AZURE_OPENAI", "ANTHROPIC", "BEDROCK"}
        else ConnectionPurpose.NOTIFICATION
    )
    connection_id = uuid4()
    encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
        validate_secret_config(provider, secret if secret_override is None else secret_override),
        connection_id=connection_id,
        environment="test",
        purpose=purpose,
        provider=provider,
    )
    with session_factory() as session, session.begin():
        session.add(
            ManagedConnectionRecord(
                id=connection_id,
                environment="test",
                purpose=purpose,
                provider=provider,
                display_name=name,
                non_secret_config=validate_non_secret_config(purpose, provider, config),
                secret_ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.nonce,
                secret_key_version=encrypted.key_version,
            )
        )
    return connection_id


@pytest.mark.parametrize("name", list(CASES))
def test_database_provider_uses_encrypted_connection_and_keeps_snapshot(
    name: str, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = database_settings()
    connection_id = add_connection(session_factory, settings, name)
    aws_calls: list[dict[str, object]] = []

    def aws_client(service: str, **kwargs: object) -> object:
        assert service == "bedrock-runtime"
        aws_calls.append(kwargs)
        return object()

    monkeypatch.setattr("dagsentry.providers.bedrock.boto3.client", aws_client)
    resolver = (
        ConfiguredLLMConnectionResolver(settings, session_factory)
        if name in {"OPENAI", "AZURE_OPENAI", "ANTHROPIC", "BEDROCK"}
        else ConfiguredNotificationConnectionResolver(settings, session_factory)
    )
    first = resolver.resolve("test")
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        record.enabled = False
        record.version += 1
    with pytest.raises(RuntimeConnectionError):
        resolver.resolve("test")
    with pytest.raises(RuntimeConnectionError):
        resolver.resolve("other-environment")
    assert first.version == 1
    assert first.connection_id == connection_id
    assert first.source == "database"
    assert first.provider is not None
    assert "secret" not in repr(first)
    for value in CASES[name][1].values():
        assert value not in repr(getattr(first.provider, "config"))
    if name == "BEDROCK":
        assert aws_calls[0]["aws_access_key_id"] == "database-access-secret"
        assert aws_calls[0]["aws_secret_access_key"] == "database-key-secret"
    elif name in {"OPENAI", "AZURE_OPENAI", "ANTHROPIC"}:
        assert getattr(first.provider, "config").api_key == "database-api-secret"
        assert getattr(first.provider, "config").model == "database-model"
    elif name == "SMTP":
        assert getattr(first.provider, "config").password == "database-password-secret"
    else:
        assert "database-url-secret" in repr(getattr(first.provider, "config").__dict__)


@pytest.mark.parametrize("name", ["WEBHOOK", "TEAMS", "DISCORD"])
def test_database_notification_delivers_to_encrypted_url(
    name: str, session_factory: SessionFactory
) -> None:
    settings = database_settings()
    connection_id = add_connection(session_factory, settings, name)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    resolver = ConfiguredNotificationConnectionResolver(
        settings, session_factory, httpx.Client(transport=httpx.MockTransport(handle))
    )
    snapshot = resolver.resolve("test", connection_id=connection_id)
    assert snapshot.provider.send(notification_payload(), delivery_key="database-delivery") == 204
    assert requests[0].url.host == "database.example"
    assert requests[0].url.params["key"] == "database-url-secret"
    with pytest.raises(RuntimeConnectionError):
        resolver.resolve("test", connection_id=uuid4())


def test_database_secret_corruption_fails_closed(session_factory: SessionFactory) -> None:
    settings = database_settings()
    connection_id = add_connection(session_factory, settings, "OPENAI")
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        record.secret_ciphertext = b"x" * 32
    with pytest.raises(RuntimeConnectionError) as raised:
        ConfiguredLLMConnectionResolver(settings, session_factory).resolve("test")
    assert "database-api-secret" not in str(raised.value)


def test_rotated_llm_secret_and_model_only_apply_to_next_snapshot(
    session_factory: SessionFactory,
) -> None:
    settings = database_settings()
    connection_id = add_connection(session_factory, settings, "OPENAI")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response_body())

    resolver = ConfiguredLLMConnectionResolver(
        settings, session_factory, httpx.Client(transport=httpx.MockTransport(handle))
    )
    first = resolver.resolve("test")
    with session_factory() as session, session.begin():
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        encrypted = ConnectionSecretCipher.from_settings(settings).encrypt(
            {"api_key": "rotated-api-secret"},
            connection_id=record.id,
            environment=record.environment,
            purpose=record.purpose,
            provider=record.provider,
        )
        record.secret_ciphertext = encrypted.ciphertext
        record.secret_nonce = encrypted.nonce
        record.secret_key_version = encrypted.key_version
        record.non_secret_config = {**record.non_secret_config, "model": "rotated-model"}
        record.version += 1
    second = resolver.resolve("test")
    assert first.provider is not None and second.provider is not None
    first.provider.diagnose(diagnosis_request())
    second.provider.diagnose(diagnosis_request())
    assert requests[0].headers["Authorization"] == "Bearer database-api-secret"
    assert requests[1].headers["Authorization"] == "Bearer rotated-api-secret"
    import json

    assert json.loads(requests[0].content)["model"] == "database-model"
    assert json.loads(requests[1].content)["model"] == "rotated-model"


@pytest.mark.parametrize("authenticated", [True, False])
def test_smtp_database_connection_delivers_with_only_its_own_credentials(
    authenticated: bool,
    session_factory: SessionFactory,
) -> None:
    from dagsentry.providers.smtp import SMTPNotificationProvider
    from tests.test_smtp_provider import FakeSMTP

    settings = database_settings()
    connection_id = add_connection(session_factory, settings, "SMTP")
    if not authenticated:
        with session_factory() as session, session.begin():
            record = session.get(ManagedConnectionRecord, connection_id)
            assert record is not None
            record.secret_ciphertext = record.secret_nonce = record.secret_key_version = None
    snapshot = ConfiguredNotificationConnectionResolver(settings, session_factory).resolve("test")
    assert isinstance(snapshot.provider, SMTPNotificationProvider)
    client = FakeSMTP()
    snapshot.provider.client_factory = lambda: client
    assert snapshot.provider.send(notification_payload(), delivery_key="smtp-key") == 250
    assert client.logins == (
        [("database-user-secret", "database-password-secret")] if authenticated else []
    )
    assert client.messages[0][0]["X-DagSentry-Delivery-Key"] == "smtp-key"
