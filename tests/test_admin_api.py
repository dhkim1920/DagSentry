from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import PasswordService, bootstrap_admin
from dagsentry.models import AdminAuditEventRecord, UserRecord, UserSessionRecord
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from dagsentry.user_management import create_user

ADMIN_PASSWORD = "correct horse battery staple"
TEMPORARY_PASSWORD = "temporary viewer password"


def bootstrap(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        bootstrap_admin(
            session,
            email="admin@example.com",
            display_name="Initial Admin",
            password=ADMIN_PASSWORD,
        )


async def login(client: httpx.AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token is not None
    return {CSRF_HEADER_NAME: csrf_token}


def test_admin_creates_updates_disables_and_audits_user_without_secret_exposure(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as admin:
            await login(admin, "admin@example.com", ADMIN_PASSWORD)
            headers = {
                **csrf_headers(admin),
                "X-Correlation-ID": "d8dce2a3-d335-429c-a3e2-37f1d2bfe182",
            }
            created = await admin.post(
                "/api/v1/admin/users",
                headers=headers,
                json={
                    "email": " VIEWER@Example.com ",
                    "display_name": "Viewer User",
                    "role": "VIEWER",
                    "temporary_password": TEMPORARY_PASSWORD,
                },
            )
            user_id = created.json()["id"]
            updated = await admin.patch(
                f"/api/v1/admin/users/{user_id}",
                headers=headers,
                json={"role": "OPERATOR"},
            )
            disabled = await admin.patch(
                f"/api/v1/admin/users/{user_id}",
                headers=headers,
                json={"status": "DISABLED"},
            )
            audit = await admin.get("/api/v1/admin/audit-events")
            return created, updated, disabled, audit

    created, updated, disabled, audit = asyncio.run(exercise())

    assert created.status_code == 201
    assert created.json()["email"] == "viewer@example.com"
    assert created.json()["must_change_password"] is True
    assert "password_hash" not in created.json()
    assert "temporary_password" not in created.json()
    assert TEMPORARY_PASSWORD not in created.text
    assert updated.status_code == 200 and updated.json()["role"] == "OPERATOR"
    assert disabled.status_code == 200 and disabled.json()["status"] == "DISABLED"
    assert audit.status_code == 200
    assert {item["action"] for item in audit.json()["items"]} >= {
        "user.created",
        "user.role_changed",
        "user.disabled",
    }
    assert TEMPORARY_PASSWORD not in audit.text
    assert audit.json()["items"][0]["actor_email"] == "admin@example.com"

    with session_factory() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.email == "viewer@example.com"))
        assert user is not None
        assert user.password_hash != TEMPORARY_PASSWORD
        assert PasswordService().verify(user.password_hash, TEMPORARY_PASSWORD)
        events = session.scalars(
            select(AdminAuditEventRecord).where(AdminAuditEventRecord.target_id == user.id)
        ).all()
        assert all(TEMPORARY_PASSWORD not in str(event.change_summary) for event in events)


def test_non_admin_cannot_use_admin_api(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    with session_factory() as session:
        admin = session.scalar(select(UserRecord))
        assert admin is not None
        admin_id = admin.id
    with session_factory() as session:
        create_user(
            session,
            actor_user_id=admin_id,
            email="operator@example.com",
            display_name="Operator",
            role=UserRole.OPERATOR,
            temporary_password=TEMPORARY_PASSWORD,
            correlation_id=None,
        )
    app = create_app(settings, session_factory)
    operator_password = "operator personal password"

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client, "operator@example.com", TEMPORARY_PASSWORD)
            changed = await client.post(
                "/api/v1/auth/change-password",
                headers=csrf_headers(client),
                json={
                    "current_password": TEMPORARY_PASSWORD,
                    "new_password": operator_password,
                },
            )
            assert changed.status_code == 204
            await login(client, "operator@example.com", operator_password)
            return await client.get("/api/v1/admin/users")

    response = asyncio.run(exercise())

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin role required"}


def test_temporary_password_session_is_restricted_until_password_change(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)
    personal_password = "viewer personal password"

    async def exercise() -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="https://test") as admin,
            httpx.AsyncClient(transport=transport, base_url="https://test") as viewer,
        ):
            await login(admin, "admin@example.com", ADMIN_PASSWORD)
            created = await admin.post(
                "/api/v1/admin/users",
                headers=csrf_headers(admin),
                json={
                    "email": "viewer@example.com",
                    "display_name": "Viewer",
                    "role": "VIEWER",
                    "temporary_password": TEMPORARY_PASSWORD,
                },
            )
            assert created.status_code == 201
            login_response = await viewer.post(
                "/api/v1/auth/login",
                json={"email": "viewer@example.com", "password": TEMPORARY_PASSWORD},
            )
            me_before = await viewer.get("/api/v1/auth/me")
            incidents_before = await viewer.get("/api/v1/incidents")
            changed = await viewer.post(
                "/api/v1/auth/change-password",
                headers=csrf_headers(viewer),
                json={
                    "current_password": TEMPORARY_PASSWORD,
                    "new_password": personal_password,
                },
            )
            relogin = await viewer.post(
                "/api/v1/auth/login",
                json={"email": "viewer@example.com", "password": personal_password},
            )
            incidents_after = await viewer.get("/api/v1/incidents")
            return login_response, me_before, incidents_before, changed, relogin, incidents_after

    login_response, me_before, incidents_before, changed, relogin, incidents_after = asyncio.run(
        exercise()
    )

    assert login_response.status_code == 200
    assert login_response.json()["user"]["must_change_password"] is True
    assert me_before.status_code == 200
    assert me_before.json()["must_change_password"] is True
    assert incidents_before.status_code == 403
    assert incidents_before.json() == {"detail": "Password change required"}
    assert changed.status_code == 204
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_change_password"] is False
    assert incidents_after.status_code == 200


def test_last_active_admin_cannot_be_disabled_or_demoted(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            admin_id = (await client.get("/api/v1/admin/users")).json()["items"][0]["id"]
            headers = csrf_headers(client)
            disabled = await client.patch(
                f"/api/v1/admin/users/{admin_id}",
                headers=headers,
                json={"status": "DISABLED"},
            )
            demoted = await client.patch(
                f"/api/v1/admin/users/{admin_id}",
                headers=headers,
                json={"role": "OPERATOR"},
            )
            return disabled, demoted

    disabled, demoted = asyncio.run(exercise())

    assert disabled.status_code == demoted.status_code == 409
    with session_factory() as session:
        admin = session.scalar(select(UserRecord))
        assert admin is not None
        assert admin.role == UserRole.ADMIN
        assert admin.status == UserStatus.ACTIVE


def test_admin_resets_password_and_revokes_user_sessions(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)
    replacement = "replacement temporary password"

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="https://test") as admin,
            httpx.AsyncClient(transport=transport, base_url="https://test") as viewer,
        ):
            await login(admin, "admin@example.com", ADMIN_PASSWORD)
            created = await admin.post(
                "/api/v1/admin/users",
                headers=csrf_headers(admin),
                json={
                    "email": "viewer@example.com",
                    "display_name": "Viewer",
                    "role": "VIEWER",
                    "temporary_password": TEMPORARY_PASSWORD,
                },
            )
            user_id = created.json()["id"]
            await login(viewer, "viewer@example.com", TEMPORARY_PASSWORD)
            reset = await admin.post(
                f"/api/v1/admin/users/{user_id}/reset-password",
                headers=csrf_headers(admin),
                json={"temporary_password": replacement},
            )
            viewer_me = await viewer.get("/api/v1/auth/me")
            relogin = await viewer.post(
                "/api/v1/auth/login",
                json={"email": "viewer@example.com", "password": replacement},
            )
            return reset, viewer_me, relogin

    reset, viewer_me, relogin = asyncio.run(exercise())

    assert reset.status_code == 200
    assert reset.json()["must_change_password"] is True
    assert viewer_me.status_code == 401
    assert relogin.status_code == 200
    with session_factory() as session:
        sessions = session.scalars(select(UserSessionRecord)).all()
        viewer = session.scalar(select(UserRecord).where(UserRecord.email == "viewer@example.com"))
        assert viewer is not None
        assert any(item.user_id == viewer.id and item.revoked_at is not None for item in sessions)


def test_admin_can_revoke_sessions_without_resetting_password(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as admin:
            await login(admin, "admin@example.com", ADMIN_PASSWORD)
            admin_id = (await admin.get("/api/v1/admin/users")).json()["items"][0]["id"]
            revoked = await admin.post(
                f"/api/v1/admin/users/{admin_id}/revoke-sessions",
                headers=csrf_headers(admin),
            )
            me = await admin.get("/api/v1/auth/me")
            return revoked, me

    revoked, me = asyncio.run(exercise())

    assert revoked.status_code == 200
    assert revoked.json() == {"sessions_revoked": 1}
    assert me.status_code == 401
