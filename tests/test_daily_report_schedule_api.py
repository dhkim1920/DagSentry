from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.identity import bootstrap_admin
from dagsentry.models import SchedulerHeartbeatRecord
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from tests.test_incident_api import VIEWER_HEADER, request

PASSWORD = "correct horse battery staple"


def bootstrap(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        bootstrap_admin(
            session,
            email="admin@example.com",
            display_name="Initial Admin",
            password=PASSWORD,
        )


def test_admin_creates_updates_and_queues_daily_report_schedule(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            assert login.status_code == 200
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf is not None
            headers = {CSRF_HEADER_NAME: csrf}
            created = await client.post(
                "/api/v1/admin/daily-report-schedules",
                headers=headers,
                json={
                    "environment": "production",
                    "enabled": True,
                    "display_name": "Production Daily Report",
                    "report_title": "DagSentry 일일 장애 리포트",
                    "use_ai_summary": True,
                    "run_at_local_time": "09:10:00",
                    "timezone": "Asia/Seoul",
                },
            )
            schedule_id = created.json()["id"]
            updated = await client.put(
                f"/api/v1/admin/daily-report-schedules/{schedule_id}",
                headers=headers,
                json={
                    "environment": "production",
                    "enabled": False,
                    "display_name": "Production Daily Report",
                    "report_title": "DagSentry 일일 장애 리포트",
                    "use_ai_summary": False,
                    "run_at_local_time": "10:10:00",
                    "timezone": "Asia/Seoul",
                    "expected_revision": 1,
                },
            )
            manual = await client.post(
                f"/api/v1/admin/daily-report-schedules/{schedule_id}/runs",
                headers=headers,
                json={"report_date": (datetime.now(UTC).date() - timedelta(days=1)).isoformat()},
            )
            second_manual = await client.post(
                f"/api/v1/admin/daily-report-schedules/{schedule_id}/runs",
                headers=headers,
                json={"report_date": (datetime.now(UTC).date() - timedelta(days=2)).isoformat()},
            )
            stale_update = await client.put(
                f"/api/v1/admin/daily-report-schedules/{schedule_id}",
                headers=headers,
                json={
                    "environment": "production",
                    "enabled": True,
                    "display_name": "Production Daily Report",
                    "report_title": "DagSentry 일일 장애 리포트",
                    "use_ai_summary": False,
                    "run_at_local_time": "10:10:00",
                    "timezone": "Asia/Seoul",
                    "expected_revision": 1,
                },
            )
            schedules = await client.get("/api/v1/daily-report-schedules")
            runs = await client.get("/api/v1/daily-report-schedules/runs?limit=1&offset=1")
            return created, updated, manual, second_manual, stale_update, schedules, runs

    created, updated, manual, second_manual, stale_update, schedules, runs = asyncio.run(exercise())

    assert created.status_code == 201
    assert created.json()["scheduler_status"] == "OFFLINE"
    assert created.json()["applied_revision"] is None
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["next_run_at"] is None
    assert manual.status_code == 202
    assert manual.json()["status"] == "CLAIMED"
    assert second_manual.status_code == 202
    assert stale_update.status_code == 409
    assert stale_update.json() == {"detail": "schedule revision conflict"}
    assert schedules.status_code == 200
    assert schedules.json()["items"][0]["enabled"] is False
    assert runs.status_code == 200
    assert runs.json()["total"] == 2
    assert runs.json()["limit"] == 1
    assert runs.json()["offset"] == 1
    assert runs.json()["items"][0]["trigger_type"] == "MANUAL"


def test_schedule_api_validates_admin_access_timezone_and_completed_date(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            unauthenticated = await client.post("/api/v1/admin/daily-report-schedules", json={})
            await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf is not None
            headers = {CSRF_HEADER_NAME: csrf}
            invalid_timezone = await client.post(
                "/api/v1/admin/daily-report-schedules",
                headers=headers,
                json={
                    "environment": "production",
                    "enabled": True,
                    "display_name": "Production Daily Report",
                    "report_title": "DagSentry 일일 장애 리포트",
                    "use_ai_summary": False,
                    "run_at_local_time": "09:10:00",
                    "timezone": "Mars/Olympus",
                },
            )
            created = await client.post(
                "/api/v1/admin/daily-report-schedules",
                headers=headers,
                json={
                    "environment": "production",
                    "enabled": True,
                    "display_name": "Production Daily Report",
                    "report_title": "DagSentry 일일 장애 리포트",
                    "use_ai_summary": False,
                    "run_at_local_time": "09:10:00",
                    "timezone": "UTC",
                },
            )
            future = await client.post(
                f"/api/v1/admin/daily-report-schedules/{created.json()['id']}/runs",
                headers=headers,
                json={"report_date": datetime.now(UTC).date().isoformat()},
            )
            return unauthenticated, invalid_timezone, future

    unauthenticated, invalid_timezone, future = asyncio.run(exercise())

    assert unauthenticated.status_code == 401
    assert invalid_timezone.status_code == 422
    assert future.status_code == 422
    assert future.json() == {"detail": "report_date must be a completed UTC date"}


def test_viewer_cannot_change_daily_report_schedule(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    response = request(
        create_app(settings, session_factory),
        "POST",
        "/api/v1/admin/daily-report-schedules",
        headers=VIEWER_HEADER,
        json={
            "environment": "production",
            "enabled": True,
            "display_name": "Production Daily Report",
            "report_title": "DagSentry 일일 장애 리포트",
            "use_ai_summary": False,
            "run_at_local_time": "09:10:00",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin role required"}


def test_scheduler_status_uses_fresh_heartbeat(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with session_factory.begin() as session:
        session.add(
            SchedulerHeartbeatRecord(
                scheduler_name="daily-report",
                instance_id=UUID("f63a5af8-86d1-4d64-92de-8a01a1b10847"),
                started_at=datetime.now(UTC),
                last_heartbeat_at=datetime.now(UTC),
                version="test",
            )
        )

    response = request(
        create_app(settings, session_factory),
        "GET",
        "/api/v1/daily-report-schedules/status",
        headers=VIEWER_HEADER,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ONLINE"
    assert response.json()["version"] == "test"
