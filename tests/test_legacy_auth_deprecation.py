import asyncio
import logging

import httpx
import pytest

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from tests.test_auth_api import PASSWORD, create_admin

HEADER = "X-DagSentry-Auth-Deprecation"


def test_legacy_auth_warns_once_at_startup_and_on_authenticated_responses(
    settings: Settings, session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="dagsentry.api"):
        app = create_app(settings, session_factory)
    warnings = [
        record
        for record in caplog.records
        if record.message.startswith("legacy_query_auth_deprecated")
    ]
    assert len(warnings) == 1
    assert "0.3" in caplog.text and "0.5" in caplog.text
    assert "test-viewer-token" not in caplog.text
    assert "test-operator-token" not in caplog.text

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            for token_header, value in [
                ("X-DagSentry-Viewer-Token", "test-viewer-token"),
                ("X-DagSentry-Operator-Token", "test-operator-token"),
            ]:
                response = await client.get("/api/v1/incidents", headers={token_header: value})
                assert response.status_code == 200
                assert "0.3" in response.headers[HEADER]
                assert "0.5" in response.headers[HEADER]
                assert value not in str(response.headers)
            denied = await client.get(
                "/api/v1/incidents", headers={"X-DagSentry-Viewer-Token": "wrong"}
            )
            assert denied.status_code == 401
            assert HEADER not in denied.headers
            health = await client.get("/health/live")
            assert HEADER not in health.headers

    asyncio.run(exercise())


def test_replacement_user_session_has_no_deprecation_warning(
    settings: Settings, session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    create_admin(session_factory)
    settings.viewer_api_token = None
    settings.operator_api_token = None
    with caplog.at_level(logging.WARNING, logger="dagsentry.api"):
        app = create_app(settings, session_factory)
    assert not [
        record
        for record in caplog.records
        if record.message.startswith("legacy_query_auth_deprecated")
    ]

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
            )
            assert response.status_code == 200
            response = await client.get("/api/v1/incidents")
            assert response.status_code == 200
            assert HEADER not in response.headers

    asyncio.run(exercise())
