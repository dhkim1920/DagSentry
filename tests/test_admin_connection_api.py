from __future__ import annotations

import asyncio
import base64
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.connection_crypto import ConnectionSecretCipher, EncryptedConnectionSecret
from dagsentry.db import SessionFactory
from dagsentry.identity import bootstrap_admin
from dagsentry.models import AdminAuditEventRecord, ManagedConnectionRecord
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

ADMIN_PASSWORD = "correct horse battery staple"
SLACK_SECRET = "xoxb-secret-must-never-leak"
REPLACEMENT_SECRET = "xoxb-replacement-must-never-leak"
KEY_BYTES = bytes(range(32))


def connection_settings(settings: Settings, *, with_key: bool = True) -> Settings:
    values = settings.model_dump()
    values["connection_encryption_key"] = (
        SecretStr(base64.b64encode(KEY_BYTES).decode()) if with_key else None
    )
    values["connection_encryption_key_version"] = 7
    return Settings(**values)


def bootstrap(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        bootstrap_admin(
            session,
            email="admin@example.com",
            display_name="Initial Admin",
            password=ADMIN_PASSWORD,
        )


async def login(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def slack_body(*, secret: object | None, expected_version: int | None = None) -> dict[str, object]:
    return {
        "environment": "production",
        "purpose": "NOTIFICATION",
        "provider": "SLACK",
        "display_name": "Production Slack",
        "non_secret_config": {"channel": "C0123456789"},
        "secret": secret,
        "enabled": True,
        "expected_version": expected_version,
    }


def test_admin_connection_api_encrypts_secret_and_never_returns_or_audits_it(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    runtime_settings = connection_settings(settings)
    bootstrap(session_factory)
    app = create_app(runtime_settings, session_factory)
    connection_id = uuid4()

    async def exercise() -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client)
            headers = csrf_headers(client)
            created = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            detail = await client.get(f"/api/v1/admin/connections/{connection_id}")
            listed = await client.get("/api/v1/admin/connections")
            audit = await client.get("/api/v1/admin/audit-events")
            return created, detail, listed, audit

    created, detail, listed, audit = asyncio.run(exercise())

    assert (
        created.status_code == detail.status_code == listed.status_code == audit.status_code == 200
    )
    assert created.json()["secret_configured"] is True
    for response in (created, detail, listed, audit):
        assert SLACK_SECRET not in response.text
        assert "secret_ciphertext" not in response.text
        assert "secret_nonce" not in response.text
        assert "secret_key_version" not in response.text

    with session_factory() as session:
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        assert record.secret_ciphertext is not None
        assert record.secret_nonce is not None
        assert record.secret_key_version == 7
        assert SLACK_SECRET.encode() not in record.secret_ciphertext
        decrypted = ConnectionSecretCipher.from_settings(runtime_settings).decrypt(
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
        assert decrypted == {"bot_token": SLACK_SECRET}
        events = session.scalars(
            select(AdminAuditEventRecord).where(AdminAuditEventRecord.target_id == connection_id)
        ).all()
        assert [event.action for event in events] == ["connection.created"]
        assert all(SLACK_SECRET not in str(event.change_summary) for event in events)


def test_update_preserves_omitted_secret_replaces_explicit_secret_and_checks_version(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    runtime_settings = connection_settings(settings)
    bootstrap(session_factory)
    app = create_app(runtime_settings, session_factory)
    connection_id = uuid4()

    async def exercise() -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client)
            headers = csrf_headers(client)
            created = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            with session_factory() as session:
                initial = session.get(ManagedConnectionRecord, connection_id)
                assert initial is not None
                initial_ciphertext = initial.secret_ciphertext
                initial_nonce = initial.secret_nonce
            preserved_body = slack_body(secret=None, expected_version=created.json()["version"])
            preserved_body["display_name"] = "Renamed Slack"
            preserved = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=preserved_body,
            )
            with session_factory() as session:
                unchanged = session.get(ManagedConnectionRecord, connection_id)
                assert unchanged is not None
                assert unchanged.secret_ciphertext == initial_ciphertext
                assert unchanged.secret_nonce == initial_nonce
            stale = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=slack_body(secret=None, expected_version=1),
            )
            replacement_body = slack_body(
                secret={"bot_token": REPLACEMENT_SECRET},
                expected_version=preserved.json()["version"],
            )
            replacement_body["display_name"] = "Renamed Slack"
            replaced = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=replacement_body,
            )
            disabled = await client.post(
                f"/api/v1/admin/connections/{connection_id}/disable",
                headers=headers,
                json={"expected_version": replaced.json()["version"]},
            )
            return created, preserved, stale, replaced, disabled

    created, preserved, stale, replaced, disabled = asyncio.run(exercise())

    assert created.status_code == preserved.status_code == replaced.status_code == 200
    assert preserved.json()["version"] == 2
    assert stale.status_code == 409
    assert replaced.json()["version"] == 3
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["version"] == 4
    for response in (preserved, stale, replaced, disabled):
        assert SLACK_SECRET not in response.text
        assert REPLACEMENT_SECRET not in response.text

    with session_factory() as session:
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        assert record.secret_ciphertext is not None
        assert record.secret_nonce is not None
        decrypted = ConnectionSecretCipher.from_settings(runtime_settings).decrypt(
            EncryptedConnectionSecret(
                record.secret_ciphertext,
                record.secret_nonce,
                record.secret_key_version or 0,
            ),
            connection_id=record.id,
            environment=record.environment,
            purpose=record.purpose,
            provider=record.provider,
        )
        assert decrypted == {"bot_token": REPLACEMENT_SECRET}
        actions = session.scalars(
            select(AdminAuditEventRecord.action).where(
                AdminAuditEventRecord.target_id == connection_id
            )
        ).all()
        assert actions == [
            "connection.created",
            "connection.updated",
            "connection.secret_replaced",
            "connection.disabled",
        ]


def test_secret_write_fails_closed_without_key_and_invalid_secret_is_not_echoed(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(connection_settings(settings, with_key=False), session_factory)
    keyed_app = create_app(connection_settings(settings), session_factory)
    secret = "do-not-echo-this-value"

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client)
            headers = csrf_headers(client)
            unavailable = await client.put(
                f"/api/v1/admin/connections/{uuid4()}",
                headers=headers,
                json=slack_body(secret={"bot_token": secret}),
            )
            ollama = await client.put(
                f"/api/v1/admin/connections/{uuid4()}",
                headers=headers,
                json={
                    "environment": "development",
                    "purpose": "LLM",
                    "provider": "OLLAMA",
                    "display_name": "Local Ollama",
                    "non_secret_config": {"model": "qwen3:8b"},
                },
            )
        keyed_transport = httpx.ASGITransport(app=keyed_app)
        async with httpx.AsyncClient(
            transport=keyed_transport,
            base_url="https://test",
        ) as client:
            await login(client)
            invalid = await client.put(
                f"/api/v1/admin/connections/{uuid4()}",
                headers=csrf_headers(client),
                json=slack_body(secret={"bot_token": secret, "unexpected": secret}),
            )
        return unavailable, ollama, invalid

    unavailable, ollama, invalid = asyncio.run(exercise())

    assert unavailable.status_code == 503
    assert secret not in unavailable.text
    assert ollama.status_code == 200
    assert ollama.json()["secret_configured"] is False
    assert invalid.status_code == 422
    assert secret not in invalid.text


def test_non_admin_cannot_read_connections(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(connection_settings(settings), session_factory)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            return await client.get(
                "/api/v1/admin/connections",
                headers={"X-DagSentry-Viewer-Token": "test-viewer-token"},
            )

    response = asyncio.run(exercise())

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin role required"}


def test_admin_runs_read_only_slack_test_and_persists_only_bounded_result(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    runtime_settings = connection_settings(settings)
    bootstrap(session_factory)
    app = create_app(runtime_settings, session_factory)
    connection_id = uuid4()
    provider_body_secret = "provider-response-must-not-leak"
    requests: list[tuple[str, str, str | None]] = []

    def passing_handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.url.params.get("channel")))
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": True, "channel": {"id": "C0123456789"}})

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client)
            headers = csrf_headers(client)
            created = await client.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=slack_body(secret={"bot_token": SLACK_SECRET}),
            )
            assert created.status_code == 200
            app.state.connection_test_http_client = httpx.Client(
                transport=httpx.MockTransport(passing_handler)
            )
            passed = await client.post(
                f"/api/v1/admin/connections/{connection_id}/test",
                headers=headers,
            )
            app.state.connection_test_http_client.close()
            app.state.connection_test_http_client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        json={
                            "ok": False,
                            "error": "invalid_auth",
                            "detail": provider_body_secret,
                        },
                    )
                )
            )
            failed = await client.post(
                f"/api/v1/admin/connections/{connection_id}/test",
                headers=headers,
            )
            app.state.connection_test_http_client.close()
            return passed, failed

    passed, failed = asyncio.run(exercise())

    assert passed.status_code == failed.status_code == 200
    assert passed.json()["status"] == "PASSED"
    assert passed.json()["error_category"] is None
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error_category"] == "AUTHENTICATION"
    assert provider_body_secret not in failed.text
    assert requests == [
        ("POST", "/api/auth.test", None),
        ("GET", "/api/conversations.info", "C0123456789"),
    ]
    with session_factory() as session:
        record = session.get(ManagedConnectionRecord, connection_id)
        assert record is not None
        assert record.last_test_status == "FAILED"
        assert record.last_test_error_category == "AUTHENTICATION"
        audit = session.scalars(
            select(AdminAuditEventRecord).where(
                AdminAuditEventRecord.target_id == connection_id,
                AdminAuditEventRecord.action == "connection.tested",
            )
        ).all()
        assert len(audit) == 2
        assert all(provider_body_secret not in str(event.change_summary) for event in audit)
