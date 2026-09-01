from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Iterator
from urllib.parse import quote_plus
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from dagsentry.api import create_app
from dagsentry.config import Settings, get_settings
from dagsentry.db import Base, SessionFactory
from dagsentry.domain.identity import UserRole
from dagsentry.identity import bootstrap_admin
from dagsentry.models import ManagedConnectionRecord, UserRecord
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME

pytestmark = pytest.mark.integration

ADMIN_PASSWORD = "postgres admin password"
TEMPORARY_PASSWORD = "postgres temporary operator password"
OPERATOR_PASSWORD = "postgres personal operator password"
INGEST_TOKEN = "postgres-ingest-token-must-not-leak"
CONNECTION_SECRET = "xoxb-postgres-secret-must-not-leak"
FIXED_SESSION = "attacker-selected-session"
FIXED_CSRF = "attacker-selected-csrf"
KEY_BYTES = bytes(range(32))

SecurityDatabase = tuple[Settings, SessionFactory, str]


@pytest.fixture
def security_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[SecurityDatabase]:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    schema = f"security_boundaries_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    schema_url = f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    original_url = os.environ.get("DAGSENTRY_DATABASE_URL")
    monkeypatch.setenv("DAGSENTRY_DATABASE_URL", schema_url)
    get_settings.cache_clear()

    application_engine = create_engine(schema_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=application_engine, expire_on_commit=False)
    runtime_settings = Settings(
        database_url=schema_url,
        environment="production",
        ingest_api_token=SecretStr(INGEST_TOKEN),
        viewer_api_token=None,
        operator_api_token=None,
        operator_api_identity=None,
        connection_encryption_key=SecretStr(base64.b64encode(KEY_BYTES).decode()),
        connection_encryption_key_version=7,
    )

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        with session_factory() as session:
            bootstrap_admin(
                session,
                email="admin@example.com",
                display_name="Initial Admin",
                password=ADMIN_PASSWORD,
            )
        yield runtime_settings, session_factory, schema
        command.downgrade(alembic_config, "base")
    finally:
        application_engine.dispose()
        if original_url is None:
            monkeypatch.delenv("DAGSENTRY_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DAGSENTRY_DATABASE_URL", original_url)
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


async def _login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def _csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def _connection_body() -> dict[str, object]:
    return {
        "environment": "production",
        "purpose": "NOTIFICATION",
        "provider": "SLACK",
        "display_name": "Production Slack",
        "non_secret_config": {"channel": "C0123456789"},
        "secret": {"bot_token": CONNECTION_SECRET},
        "enabled": True,
    }


def _logical_database_dump(session_factory: SessionFactory, schema: str) -> str:
    rows: list[str] = []
    with session_factory() as session:
        bind = session.get_bind()
        quote = bind.dialect.identifier_preparer.quote_identifier
        for table in Base.metadata.sorted_tables:
            statement = text(
                f"SELECT row_to_json(record)::text "
                f"FROM {quote(schema)}.{quote(table.name)} AS record"
            )
            rows.extend(str(value) for value in session.scalars(statement))
    return "\n".join(rows)


def test_postgres_rejects_session_fixation_cross_session_csrf_and_role_escalation(
    security_database: SecurityDatabase,
) -> None:
    settings, session_factory, _ = security_database
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[str, str, str, str]:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="https://test") as admin,
            httpx.AsyncClient(transport=transport, base_url="https://test") as peer,
            httpx.AsyncClient(transport=transport, base_url="https://test") as attacker,
            httpx.AsyncClient(transport=transport, base_url="https://test") as operator,
        ):
            admin.cookies.set(SESSION_COOKIE_NAME, FIXED_SESSION, domain="test.local", path="/")
            admin.cookies.set(CSRF_COOKIE_NAME, FIXED_CSRF, domain="test.local", path="/")
            login = await _login(admin, "admin@example.com", ADMIN_PASSWORD)
            assert login.status_code == 200
            admin_session = admin.cookies.get(SESSION_COOKIE_NAME)
            admin_csrf = admin.cookies.get(CSRF_COOKIE_NAME)
            assert admin_session is not None and admin_session != FIXED_SESSION
            assert admin_csrf is not None and admin_csrf != FIXED_CSRF

            attacker.cookies.set(
                SESSION_COOKIE_NAME,
                FIXED_SESSION,
                domain="test.local",
                path="/",
            )
            attacker.cookies.set(
                CSRF_COOKIE_NAME,
                FIXED_CSRF,
                domain="test.local",
                path="/",
            )
            assert (await attacker.get("/api/v1/auth/me")).status_code == 401

            peer_login = await _login(peer, "admin@example.com", ADMIN_PASSWORD)
            assert peer_login.status_code == 200
            peer_csrf = peer.cookies.get(CSRF_COOKIE_NAME)
            assert peer_csrf is not None and peer_csrf != admin_csrf

            user_body = {
                "email": "operator@example.com",
                "display_name": "Operator",
                "role": "OPERATOR",
                "temporary_password": TEMPORARY_PASSWORD,
            }
            cross_session = await admin.post(
                "/api/v1/admin/users",
                headers={CSRF_HEADER_NAME: peer_csrf},
                json=user_body,
            )
            assert cross_session.status_code == 403
            assert cross_session.json() == {"detail": "Invalid CSRF token"}

            created = await admin.post(
                "/api/v1/admin/users",
                headers={CSRF_HEADER_NAME: admin_csrf},
                json=user_body,
            )
            assert created.status_code == 201
            operator_id = created.json()["id"]

            operator_login = await _login(operator, "operator@example.com", TEMPORARY_PASSWORD)
            assert operator_login.status_code == 200
            changed = await operator.post(
                "/api/v1/auth/change-password",
                headers=_csrf_headers(operator),
                json={
                    "current_password": TEMPORARY_PASSWORD,
                    "new_password": OPERATOR_PASSWORD,
                },
            )
            assert changed.status_code == 204
            operator_relogin = await _login(operator, "operator@example.com", OPERATOR_PASSWORD)
            assert operator_relogin.status_code == 200
            escalation = await operator.patch(
                f"/api/v1/admin/users/{operator_id}",
                headers=_csrf_headers(operator),
                json={"role": "ADMIN"},
            )
            assert escalation.status_code == 403
            assert escalation.json() == {"detail": "Admin role required"}

            operator_session = operator.cookies.get(SESSION_COOKIE_NAME)
            operator_csrf = operator.cookies.get(CSRF_COOKIE_NAME)
            assert operator_session is not None
            assert operator_csrf is not None
            return admin_session, admin_csrf, operator_session, operator_csrf

    raw_session_values = asyncio.run(exercise())

    with session_factory() as session:
        operator_record = session.scalar(
            select(UserRecord).where(UserRecord.email == "operator@example.com")
        )
        assert operator_record is not None
        assert operator_record.role == UserRole.OPERATOR

    database_dump = _logical_database_dump(session_factory, security_database[2])
    for value in (FIXED_SESSION, FIXED_CSRF, *raw_session_values):
        assert value not in database_dump


def test_postgres_keeps_plaintext_secrets_out_of_storage_responses_logs_and_metrics(
    security_database: SecurityDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, session_factory, schema = security_database
    app = create_app(settings, session_factory)
    connection_id = uuid4()
    caplog.set_level(logging.INFO)

    async def exercise() -> tuple[str, str, str, str]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as admin:
            login = await _login(admin, "admin@example.com", ADMIN_PASSWORD)
            assert login.status_code == 200
            session_token = admin.cookies.get(SESSION_COOKIE_NAME)
            csrf_token = admin.cookies.get(CSRF_COOKIE_NAME)
            assert session_token is not None
            assert csrf_token is not None
            headers = {CSRF_HEADER_NAME: csrf_token}

            created_user = await admin.post(
                "/api/v1/admin/users",
                headers=headers,
                json={
                    "email": "viewer@example.com",
                    "display_name": "Viewer",
                    "role": "VIEWER",
                    "temporary_password": TEMPORARY_PASSWORD,
                },
            )
            created_connection = await admin.put(
                f"/api/v1/admin/connections/{connection_id}",
                headers=headers,
                json=_connection_body(),
            )
            responses = (
                created_user,
                created_connection,
                await admin.get("/api/v1/admin/users"),
                await admin.get("/api/v1/admin/connections"),
                await admin.get(f"/api/v1/admin/connections/{connection_id}"),
                await admin.get("/api/v1/admin/audit-events"),
            )
            assert all(response.status_code in {200, 201} for response in responses)
            metrics = await admin.get("/metrics")
            assert metrics.status_code == 200
            return (
                "\n".join(response.text for response in responses),
                metrics.text,
                session_token,
                csrf_token,
            )

    api_bodies, metrics_body, session_token, csrf_token = asyncio.run(exercise())

    with session_factory() as session:
        connection = session.get(ManagedConnectionRecord, connection_id)
        assert connection is not None
        assert connection.secret_ciphertext is not None
        assert CONNECTION_SECRET.encode() not in connection.secret_ciphertext

    database_dump = _logical_database_dump(session_factory, schema)
    encryption_key = settings.connection_encryption_key
    assert encryption_key is not None
    forbidden_values = (
        ADMIN_PASSWORD,
        TEMPORARY_PASSWORD,
        INGEST_TOKEN,
        CONNECTION_SECRET,
        session_token,
        csrf_token,
        encryption_key.get_secret_value(),
    )
    surfaces = {
        "database dump": database_dump,
        "API responses": api_bodies,
        "application logs": caplog.text,
        "Prometheus metrics": metrics_body,
    }
    for surface_name, surface in surfaces.items():
        for value in forbidden_values:
            assert value not in surface, f"plaintext Secret leaked through {surface_name}"
