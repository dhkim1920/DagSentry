from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from urllib.parse import quote_plus
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, select, text

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import Base, SessionFactory, create_session_factory
from dagsentry.identity import bootstrap_admin
from dagsentry.models import (
    AdminAuditEventRecord,
    DailyReportScheduleRecord,
    SchedulerHeartbeatRecord,
)
from dagsentry.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from tests.test_incident_api import VIEWER_HEADER, request

PASSWORD = "correct horse battery staple"


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def concurrent_schedule_factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[SessionFactory]:
    if request.param == "sqlite":
        factory = create_session_factory(f"sqlite+pysqlite:///{tmp_path / 'schedules.sqlite3'}")
        engine = factory.kw["bind"]
        Base.metadata.create_all(engine)
        try:
            yield factory
        finally:
            engine.dispose()
        return

    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    schema = f"schedule_updates_{uuid4().hex}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    factory = create_session_factory(
        f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    )
    try:
        Base.metadata.create_all(factory.kw["bind"])
        yield factory
    finally:
        factory.kw["bind"].dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


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
    assert future.json() == {
        "detail": "report_date must be a completed date in the schedule timezone"
    }


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


@pytest.mark.parametrize("run_at_local_time", ["09:10:00+09:00", "09:10:00Z"])
def test_schedule_rejects_time_offsets_on_create_and_update(
    settings: Settings,
    session_factory: SessionFactory,
    run_at_local_time: str,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
            )
            assert login.status_code == 200
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf is not None
            headers = {CSRF_HEADER_NAME: csrf}
            body = {
                "environment": "production",
                "enabled": True,
                "display_name": "Production Daily Report",
                "report_title": "Daily Report",
                "run_at_local_time": "09:10:00",
                "timezone": "UTC",
            }
            created = await client.post(
                "/api/v1/admin/daily-report-schedules", headers=headers, json=body
            )
            assert created.status_code == 201
            invalid = {**body, "run_at_local_time": run_at_local_time}
            responses = [
                await client.post(
                    "/api/v1/admin/daily-report-schedules",
                    headers=headers,
                    json={**invalid, "environment": "offset-rejected"},
                ),
                await client.put(
                    f"/api/v1/admin/daily-report-schedules/{created.json()['id']}",
                    headers=headers,
                    json={**invalid, "expected_revision": 1},
                ),
            ]
            assert [response.status_code for response in responses] == [422, 422]
            saved = await client.get(f"/api/v1/admin/daily-report-schedules/{created.json()['id']}")
            assert saved.json()["revision"] == 1
            assert saved.json()["run_at_local_time"] == "09:10:00"

    asyncio.run(exercise())


def test_concurrent_schedule_updates_preserve_winner_and_audit(
    settings: Settings,
    concurrent_schedule_factory: SessionFactory,
) -> None:
    factory = concurrent_schedule_factory
    bootstrap(factory)
    app = create_app(settings, factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
            )
            assert login.status_code == 200
            csrf = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf is not None
            headers = {CSRF_HEADER_NAME: csrf}
            body = {
                "environment": "production",
                "enabled": True,
                "display_name": "Initial schedule",
                "report_title": "Daily Report",
                "run_at_local_time": "09:10:00",
                "timezone": "UTC",
            }
            created = await client.post(
                "/api/v1/admin/daily-report-schedules", headers=headers, json=body
            )
            assert created.status_code == 201
            schedule_id = UUID(created.json()["id"])
            barrier = Barrier(2)

            def align_reads(record: DailyReportScheduleRecord, context: object) -> None:
                if record.id == schedule_id:
                    barrier.wait(timeout=10)

            event.listen(DailyReportScheduleRecord, "load", align_reads)
            try:
                responses = await asyncio.gather(
                    *[
                        client.put(
                            f"/api/v1/admin/daily-report-schedules/{schedule_id}",
                            headers=headers,
                            json={**body, "display_name": name, "expected_revision": 1},
                        )
                        for name in ("First writer", "Second writer")
                    ]
                )
            finally:
                event.remove(DailyReportScheduleRecord, "load", align_reads)
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = next(response.json() for response in responses if response.status_code == 200)
            saved = await client.get(f"/api/v1/admin/daily-report-schedules/{schedule_id}")
            assert saved.json()["revision"] == 2
            assert saved.json()["display_name"] == winner["display_name"]
            with factory() as session:
                audits = session.scalars(
                    select(AdminAuditEventRecord).where(
                        AdminAuditEventRecord.target_id == schedule_id,
                        AdminAuditEventRecord.action == "daily_report_schedule.updated",
                    )
                ).all()
                assert len(audits) == 1
                before = audits[0].change_summary["before"]
                after = audits[0].change_summary["after"]
                assert isinstance(before, dict) and isinstance(after, dict)
                assert before["display_name"] == "Initial schedule"
                assert after["display_name"] == winner["display_name"]

    asyncio.run(exercise())
