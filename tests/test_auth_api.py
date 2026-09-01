from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.identity import PasswordService, SessionService, bootstrap_admin
from dagsentry.incident import transition_incident
from dagsentry.models import (
    AdminAuditEventRecord,
    IncidentStateTransitionRecord,
    OperationalMetricCounterRecord,
    UserRecord,
    UserSessionRecord,
)
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from tests.test_incident_api import create_incident

PASSWORD = "correct horse battery staple"


def create_admin(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        bootstrap_admin(
            session,
            email="admin@example.com",
            display_name="Initial Admin",
            password=PASSWORD,
        )


def test_login_sets_hash_only_secure_cookies_and_me_returns_identity(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response, str, str]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": " ADMIN@example.com ", "password": PASSWORD},
            )
            session_token = client.cookies.get(SESSION_COOKIE_NAME)
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert session_token is not None
            assert csrf_token is not None
            me = await client.get("/api/v1/auth/me")
            return login, me, session_token, csrf_token

    login, me, session_token, csrf_token = asyncio.run(exercise())

    assert login.status_code == 200
    assert login.json() == {
        "user": {
            "id": login.json()["user"]["id"],
            "email": "admin@example.com",
            "display_name": "Initial Admin",
            "role": "ADMIN",
            "must_change_password": False,
        }
    }
    assert "token" not in login.text
    cookies = login.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith(f"{SESSION_COOKIE_NAME}="))
    csrf_cookie = next(value for value in cookies if value.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "Secure" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"

    with session_factory() as session:
        stored = session.scalar(select(UserSessionRecord))
        assert stored is not None
        assert stored.token_hash == hashlib.sha256(session_token.encode()).hexdigest()
        assert stored.csrf_token_hash == hashlib.sha256(csrf_token.encode()).hexdigest()
        assert session_token not in repr(stored)
        assert csrf_token not in repr(stored)


def test_login_failure_is_generic_and_counted_without_session_cookie(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            unknown = await client.post(
                "/api/v1/auth/login",
                json={"email": "unknown@example.com", "password": "incorrect password"},
            )
            incorrect = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": "incorrect password"},
            )
            assert client.cookies.get(SESSION_COOKIE_NAME) is None
            return unknown, incorrect

    unknown, incorrect = asyncio.run(exercise())

    assert unknown.status_code == incorrect.status_code == 401
    assert unknown.json() == incorrect.json() == {"detail": "Invalid email or password"}
    with session_factory() as session:
        counter = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_auth_events_total", "login_failure"),
        )
        assert counter is not None and counter.value == 2


def test_login_rate_limit_rejects_before_another_password_verification(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    settings.login_rate_limit_attempts = 2
    create_admin(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            return [
                await client.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@example.com", "password": "incorrect password"},
                )
                for _ in range(3)
            ]

    first, second, limited = asyncio.run(exercise())

    assert first.status_code == second.status_code == 401
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many login attempts; try again later"}


def test_session_state_change_requires_csrf_and_admin_uses_email_as_actor(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    with session_factory() as session:
        incident_id, _ = create_incident(session)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            assert login.status_code == 200
            body = {"status": "ACKNOWLEDGED", "expected_status": "OPEN"}
            missing_csrf = await client.patch(
                f"/api/v1/incidents/{incident_id}/status",
                json=body,
            )
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf_token is not None
            changed = await client.patch(
                f"/api/v1/incidents/{incident_id}/status",
                json=body,
                headers={CSRF_HEADER_NAME: csrf_token},
            )
            ambiguous = await client.get(
                "/api/v1/incidents",
                headers={"X-DagSentry-Operator-Token": "test-operator-token"},
            )
            return missing_csrf, changed, ambiguous

    missing_csrf, changed, ambiguous = asyncio.run(exercise())

    assert missing_csrf.status_code == 403
    assert missing_csrf.json() == {"detail": "Invalid CSRF token"}
    assert changed.status_code == 200
    assert ambiguous.status_code == 400
    with session_factory() as session:
        transition = session.scalar(select(IncidentStateTransitionRecord))
        assert transition is not None and transition.actor == "admin@example.com"


def test_admin_can_change_a_terminal_incident_created_by_another_operator(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    with session_factory() as session:
        incident_id, _ = create_incident(session)
        transition_incident(
            session,
            incident_id=incident_id,
            target=IncidentStatus.RESOLVED,
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor="other@example.com",
        )
    app = create_app(settings, session_factory)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            assert login.status_code == 200
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf_token is not None
            return await client.patch(
                f"/api/v1/incidents/{incident_id}/status",
                json={"status": "OPEN", "expected_status": "RESOLVED"},
                headers={CSRF_HEADER_NAME: csrf_token},
            )

    changed = asyncio.run(exercise())

    assert changed.status_code == 200
    assert changed.json()["status"] == "OPEN"


def test_logout_revokes_session_and_expires_both_cookies(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf_token is not None
            logout = await client.post(
                "/api/v1/auth/logout",
                headers={CSRF_HEADER_NAME: csrf_token},
            )
            me = await client.get("/api/v1/auth/me")
            return logout, me

    logout, me = asyncio.run(exercise())

    assert logout.status_code == 204
    assert me.status_code == 401
    with session_factory() as session:
        stored = session.scalar(select(UserSessionRecord))
        assert stored is not None and stored.revoked_at is not None


def test_change_password_revokes_sessions_and_records_correlated_audit(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    app = create_app(settings, session_factory)
    new_password = "a newly changed secure password"

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf_token is not None
            headers = {
                CSRF_HEADER_NAME: csrf_token,
                "X-Correlation-ID": "d8dce2a3-d335-429c-a3e2-37f1d2bfe182",
            }
            incorrect = await client.post(
                "/api/v1/auth/change-password",
                headers=headers,
                json={"current_password": "incorrect password", "new_password": new_password},
            )
            changed = await client.post(
                "/api/v1/auth/change-password",
                headers=headers,
                json={"current_password": PASSWORD, "new_password": new_password},
            )
            old_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            return incorrect, changed, old_login

    incorrect, changed, old_login = asyncio.run(exercise())

    assert incorrect.status_code == 400
    assert changed.status_code == 204
    assert old_login.status_code == 401
    with session_factory() as session:
        audit = session.scalar(
            select(AdminAuditEventRecord).where(
                AdminAuditEventRecord.action == "user.password_changed"
            )
        )
        assert audit is not None
        assert audit.correlation_id == "d8dce2a3-d335-429c-a3e2-37f1d2bfe182"
        assert audit.change_summary == {"sessions_revoked": 1}
        session_record = session.scalar(select(UserSessionRecord))
        assert session_record is not None and session_record.revoked_at is not None
        user = session.scalar(select(UserRecord))
        assert user is not None
        assert PasswordService().verify(user.password_hash, new_password)


def test_expired_session_is_rejected(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    create_admin(session_factory)
    with session_factory() as session:
        admin = session.scalar(select(UserRecord))
        assert admin is not None
        issued = SessionService(timedelta(seconds=1)).issue(
            session,
            admin.id,
            now=datetime.now(UTC) - timedelta(minutes=1),
        )
    app = create_app(settings, session_factory)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            client.cookies.set(SESSION_COOKIE_NAME, issued.token, domain="test", path="/")
            return await client.get("/api/v1/auth/me")

    assert asyncio.run(exercise()).status_code == 401
