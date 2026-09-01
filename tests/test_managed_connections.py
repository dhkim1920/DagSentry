from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dagsentry.connection_config import (
    ConnectionConfigError,
    validate_non_secret_config,
    validate_secret_config,
)
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.models import ManagedConnectionRecord


@pytest.mark.parametrize(
    ("purpose", "provider", "config", "expected_field"),
    [
        (
            ConnectionPurpose.AIRFLOW,
            ConnectionProvider.AIRFLOW,
            {
                "api_base_url": "https://airflow.example/api/v2",
                "ui_base_url": "https://airflow.example",
            },
            "api_base_url",
        ),
        (
            ConnectionPurpose.LLM,
            ConnectionProvider.OLLAMA,
            {"api_base_url": "http://ollama:11434/api", "model": "qwen3:8b"},
            "model",
        ),
        (
            ConnectionPurpose.LLM,
            ConnectionProvider.OPENAI,
            {"model": "gpt-5-mini"},
            "model",
        ),
        (
            ConnectionPurpose.LLM,
            ConnectionProvider.AZURE_OPENAI,
            {"endpoint": "https://example.openai.azure.com", "model": "diagnosis"},
            "endpoint",
        ),
        (
            ConnectionPurpose.LLM,
            ConnectionProvider.ANTHROPIC,
            {"model": "claude-sonnet"},
            "api_version",
        ),
        (
            ConnectionPurpose.LLM,
            ConnectionProvider.BEDROCK,
            {"region": "ap-northeast-2", "model_id": "anthropic.claude"},
            "region",
        ),
        (
            ConnectionPurpose.NOTIFICATION,
            ConnectionProvider.WEBHOOK,
            {},
            "timeout_seconds",
        ),
        (
            ConnectionPurpose.NOTIFICATION,
            ConnectionProvider.SLACK,
            {"channel": "C12345678"},
            "channel",
        ),
        (
            ConnectionPurpose.NOTIFICATION,
            ConnectionProvider.TEAMS,
            {},
            "max_attempts",
        ),
        (
            ConnectionPurpose.NOTIFICATION,
            ConnectionProvider.DISCORD,
            {},
            "retry_backoff_seconds",
        ),
    ],
)
def test_provider_configs_are_allowlisted_and_canonicalized(
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
    config: dict[str, object],
    expected_field: str,
) -> None:
    validated = validate_non_secret_config(purpose, provider, config)

    assert expected_field in validated
    assert (
        not {"api_key", "bot_token", "bearer_token", "password", "credentials"} & validated.keys()
    )


def test_config_rejects_provider_mismatch_unknown_fields_and_secret_bearing_urls() -> None:
    secret = "must-not-appear"

    with pytest.raises(ConnectionConfigError, match="not a valid AIRFLOW"):
        validate_non_secret_config(
            ConnectionPurpose.AIRFLOW,
            ConnectionProvider.SLACK,
            {"channel": "alerts"},
        )
    with pytest.raises(ConnectionConfigError) as unknown_error:
        validate_non_secret_config(
            ConnectionPurpose.NOTIFICATION,
            ConnectionProvider.SLACK,
            {"channel": "alerts", "bot_token": secret},
        )
    with pytest.raises(ConnectionConfigError) as url_error:
        validate_non_secret_config(
            ConnectionPurpose.LLM,
            ConnectionProvider.OLLAMA,
            {
                "api_base_url": f"https://user:{secret}@ollama.example/api?token={secret}",
                "model": "qwen3:8b",
            },
        )

    assert secret not in str(unknown_error.value)
    assert secret not in str(url_error.value)


def test_azure_openai_requires_https() -> None:
    with pytest.raises(ConnectionConfigError):
        validate_non_secret_config(
            ConnectionPurpose.LLM,
            ConnectionProvider.AZURE_OPENAI,
            {"endpoint": "http://azure.example", "model": "diagnosis"},
        )


def test_credential_connection_endpoints_require_https_unless_explicitly_trusted() -> None:
    with pytest.raises(ConnectionConfigError):
        validate_non_secret_config(
            ConnectionPurpose.LLM,
            ConnectionProvider.OPENAI,
            {"api_base_url": "http://api.openai.example/v1", "model": "diagnosis"},
        )

    validated = validate_non_secret_config(
        ConnectionPurpose.LLM,
        ConnectionProvider.OPENAI,
        {
            "api_base_url": "http://10.0.0.15/v1",
            "model": "diagnosis",
            "trusted_http_hosts": ["10.0.0.15"],
        },
    )

    assert validated["trusted_http_hosts"] == ["10.0.0.15"]


@pytest.mark.parametrize(
    ("provider", "secret", "expected"),
    [
        (ConnectionProvider.AIRFLOW, {"token": "value"}, {"token": "value"}),
        (ConnectionProvider.OPENAI, {"api_key": "value"}, {"api_key": "value"}),
        (
            ConnectionProvider.BEDROCK,
            {"access_key_id": "id", "secret_access_key": "secret"},
            {"access_key_id": "id", "secret_access_key": "secret"},
        ),
        (
            ConnectionProvider.WEBHOOK,
            {"url": "https://hooks.example/path?signature=value"},
            {"url": "https://hooks.example/path?signature=value"},
        ),
        (ConnectionProvider.SLACK, {"bot_token": "value"}, {"bot_token": "value"}),
        (
            ConnectionProvider.TEAMS,
            {"webhook_url": "https://teams.example/hook?signature=value"},
            {"webhook_url": "https://teams.example/hook?signature=value"},
        ),
        (
            ConnectionProvider.DISCORD,
            {"webhook_url": "https://discord.example/hook/token"},
            {"webhook_url": "https://discord.example/hook/token"},
        ),
    ],
)
def test_provider_secret_configs_are_write_only_allowlists(
    provider: ConnectionProvider,
    secret: dict[str, object],
    expected: dict[str, str],
) -> None:
    assert validate_secret_config(provider, secret) == expected


def test_secret_config_validation_never_echoes_input() -> None:
    secret = "must-not-appear"

    with pytest.raises(ConnectionConfigError) as error:
        validate_secret_config(
            ConnectionProvider.SLACK,
            {"bot_token": secret, "unexpected": secret},
        )
    with pytest.raises(ConnectionConfigError, match="does not accept"):
        validate_secret_config(ConnectionProvider.OLLAMA, {"token": secret})

    assert secret not in str(error.value)


def test_managed_connection_allows_only_one_purpose_per_environment(session: Session) -> None:
    session.add(
        connection_record(
            purpose=ConnectionPurpose.LLM,
            provider=ConnectionProvider.OLLAMA,
        )
    )
    session.commit()
    session.add(
        connection_record(
            purpose=ConnectionPurpose.LLM,
            provider=ConnectionProvider.OPENAI,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_database_rejects_provider_purpose_mismatch_and_partial_ciphertext(
    session: Session,
) -> None:
    mismatch = connection_record(
        purpose=ConnectionPurpose.AIRFLOW,
        provider=ConnectionProvider.SLACK,
    )
    session.add(mismatch)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    partial_secret = connection_record(
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
    )
    partial_secret.secret_ciphertext = b"ciphertext"
    session.add(partial_secret)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    malformed_secret = connection_record(
        purpose=ConnectionPurpose.NOTIFICATION,
        provider=ConnectionProvider.SLACK,
    )
    malformed_secret.secret_ciphertext = b"ciphertext-with-auth-tag"
    malformed_secret.secret_nonce = b"too-short"
    malformed_secret.secret_key_version = 1
    session.add(malformed_secret)
    with pytest.raises(IntegrityError):
        session.commit()


def connection_record(
    *,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
) -> ManagedConnectionRecord:
    return ManagedConnectionRecord(
        id=uuid4(),
        environment="production",
        purpose=purpose,
        provider=provider,
        display_name=f"{provider.value} production",
        non_secret_config={},
        enabled=True,
        version=1,
    )
