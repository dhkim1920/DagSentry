from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.connection_crypto import ConnectionSecretCipher
from dagsentry.db import SessionFactory
from tests.test_admin_connection_api import (
    SLACK_SECRET,
    bootstrap,
    connection_settings,
    csrf_headers,
    login,
    slack_body,
)


def test_saved_connection_does_not_override_environment_source(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    configured = connection_settings(settings).model_copy(
        update={
            "notification_provider": "teams",
            "teams_webhook_url": "https://private.invalid/private-token",
            "notification_fallback_providers": ["smtp"],
        }
    )
    app = create_app(configured, session_factory)
    connection_id = uuid4()

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client)
            response = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=csrf_headers(client),
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            assert response.status_code == 200
            response = await client.get("/api/v1/admin/connections")
            runtime = response.json()["runtime_configuration"]
            assert runtime["scope"] == "api"
            assert runtime["environment"] == "production"
            selected = {item["purpose"]: item for item in runtime["selections"]}
            notification = selected["NOTIFICATION"]
            assert notification["source"] == "environment"
            assert notification["provider"] == "TEAMS"
            assert notification["connection_id"] is None
            assert notification["version"] is None
            assert notification["fallback_providers"] == ["SMTP"]
            assert selected["LLM"]["provider"] is None
            assert "private-token" not in response.text
            assert SLACK_SECRET not in response.text

    asyncio.run(exercise())


def test_database_selection_tracks_version_disable_and_missing_purpose_even_off_page(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    configured = connection_settings(settings).model_copy(
        update={
            "notification_config_source": "database",
            "airflow_config_source": "database",
            "notification_fallback_providers": ["smtp"],
        }
    )
    app = create_app(configured, session_factory)
    connection_id = uuid4()

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client)
            response = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=csrf_headers(client),
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            assert response.status_code == 200
            # The selection summary must not depend on the visible connection page.
            response = await client.get("/api/v1/admin/connections?limit=1&offset=1")
            assert response.json()["items"] == []
            runtime = response.json()["runtime_configuration"]
            assert runtime["encryption_key_configured"] is True
            selected = {item["purpose"]: item for item in runtime["selections"]}
            assert selected["NOTIFICATION"]["connection_id"] == str(connection_id)
            assert selected["NOTIFICATION"]["version"] == 1
            assert selected["NOTIFICATION"]["provider"] == "SLACK"
            assert selected["NOTIFICATION"]["fallback_providers"] == []
            assert selected["AIRFLOW"]["connection_id"] is None
            assert selected["AIRFLOW"]["source"] == "database"
            updated_body = slack_body(secret=None, expected_version=1)
            updated_body["display_name"] = "Updated Slack"
            response = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=csrf_headers(client),
                json=updated_body,
            )
            assert response.status_code == 200
            response = await client.get("/api/v1/admin/connections")
            assert (
                next(
                    item
                    for item in response.json()["runtime_configuration"]["selections"]
                    if item["purpose"] == "NOTIFICATION"
                )["version"]
                == 2
            )
            response = await client.post(
                f"/api/v1/admin/connections/{connection_id}/disable",
                headers=csrf_headers(client),
                json={"expected_version": 2},
            )
            assert response.status_code == 200
            response = await client.get("/api/v1/admin/connections")
            selected = next(
                item
                for item in response.json()["runtime_configuration"]["selections"]
                if item["purpose"] == "NOTIFICATION"
            )
            assert selected["connection_id"] is None
            assert selected["provider"] is None

    asyncio.run(exercise())


def test_selection_is_local_metadata_without_provider_creation_or_secret_decryption(
    settings: Settings,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap(session_factory)
    # Invalid external credentials and encryption configuration must not trigger
    # provider construction, decryption, AWS metadata lookup, or a test request.
    configured = settings.model_copy(
        update={
            "llm_provider": "bedrock",
            "bedrock_region": "ap-northeast-2",
            "llm_model": "test-model",
            "notification_provider": "slack",
            "notification_config_source": "database",
            "slack_bot_token": SecretStr("private-token"),
            "connection_encryption_key": None,
        }
    )
    app = create_app(connection_settings(settings), session_factory)
    connection_id = uuid4()

    def unexpected_external_operation(*_: object, **__: object) -> None:
        raise AssertionError("Selection must only inspect local, non-secret metadata")

    monkeypatch.setattr("boto3.client", unexpected_external_operation)
    monkeypatch.setattr("httpx.Client.send", unexpected_external_operation)
    monkeypatch.setattr(ConnectionSecretCipher, "decrypt", unexpected_external_operation)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client)
            response = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=csrf_headers(client),
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            assert response.status_code == 200
            app.state.settings = configured
            response = await client.get("/api/v1/admin/connections")
            assert response.status_code == 200
            runtime = response.json()["runtime_configuration"]
            assert runtime["encryption_key_configured"] is False
            selected = {item["purpose"]: item for item in runtime["selections"]}
            assert selected["LLM"]["provider"] == "BEDROCK"
            assert selected["NOTIFICATION"]["connection_id"] == str(connection_id)
            assert selected["NOTIFICATION"]["secret_configured"] is True
            assert "private-token" not in response.text
            assert SLACK_SECRET not in response.text
            app.state.settings = configured.model_copy(
                update={"environment": "another-environment"}
            )
            response = await client.get("/api/v1/admin/connections")
            runtime = response.json()["runtime_configuration"]
            assert runtime["environment"] == "another-environment"
            assert (
                next(item for item in runtime["selections"] if item["purpose"] == "NOTIFICATION")[
                    "connection_id"
                ]
                is None
            )

    asyncio.run(exercise())
