from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from dagsentry import models
from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.identity import UserRole
from dagsentry.models import (
    DiagnosisOutboxRecord,
    OutboxStatus,
    SchedulerHeartbeatRecord,
    UserRecord,
)
from tests.test_admin_api import ADMIN_PASSWORD, bootstrap, login
from tests.test_worker import add_job, get_outbox

ROOT = "/api/v1/admin/operations"


def test_empty_operational_snapshot_does_not_invent_live_workers(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            response = await client.get(ROOT)
            assert response.status_code == 200
            payload = response.json()
            assert payload["api_status"] == "ONLINE"
            assert payload["database_status"] == "READY"
            assert payload["worker"]["status"] == "UNKNOWN"
            assert payload["worker"]["online_instances"] == 0
            assert payload["worker"]["last_success_at"] is None
            assert payload["scheduler"]["status"] == "UNKNOWN"
            assert payload["queue"] == {
                "pending": 0,
                "ready": 0,
                "delayed": 0,
                "processing": 0,
                "stale_processing": 0,
                "dead": 0,
                "completed": 0,
                "oldest_ready_at": None,
                "oldest_ready_age_seconds": 0,
            }
            assert "no-store" in response.headers["cache-control"]
            assert "database_url" not in response.text

    asyncio.run(exercise())


def test_queue_distinguishes_due_delayed_and_stale_jobs_and_reports_last_completion(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    now = datetime.now(UTC)
    for index, state in enumerate(
        [
            OutboxStatus.PENDING,
            OutboxStatus.PENDING,
            OutboxStatus.PROCESSING,
            OutboxStatus.PROCESSING,
            OutboxStatus.DEAD,
            OutboxStatus.COMPLETED,
        ]
    ):
        failure_id = add_job(session_factory, task_id=f"status-{index}")
        job_id = get_outbox(session_factory, failure_id).id
        with session_factory.begin() as session:
            job = session.get(DiagnosisOutboxRecord, job_id)
            assert job is not None
            job.status = state
            job.available_at = (
                now + timedelta(minutes=5) if index == 1 else now - timedelta(seconds=90)
            )
            job.locked_at = now - timedelta(seconds=901 if index == 2 else 5)
            job.last_error = "private-original-error-token"
            job.updated_at = now - timedelta(seconds=15)
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            response = await client.get(ROOT)
            assert response.status_code == 200
            payload = response.json()
            queue = payload["queue"]
            assert (queue["pending"], queue["ready"], queue["delayed"]) == (2, 1, 1)
            assert (
                queue["processing"],
                queue["stale_processing"],
                queue["dead"],
                queue["completed"],
            ) == (2, 1, 1, 1)
            assert 90 <= queue["oldest_ready_age_seconds"] < 100
            assert payload["worker"]["last_success_at"] is not None
            assert payload["worker"]["status"] == "UNKNOWN"
            assert "private-original-error-token" not in response.text
            assert "last_error" not in response.text

    asyncio.run(exercise())


@pytest.mark.parametrize("fresh", [True, False])
def test_worker_instances_and_scheduler_are_classified_by_freshness(
    settings: Settings,
    session_factory: SessionFactory,
    fresh: bool,
) -> None:
    bootstrap(session_factory)
    now = datetime.now(UTC)
    age = timedelta(seconds=1 if fresh else 60)
    with session_factory.begin() as session:
        session.add(
            models.DailyReportScheduleRunRecord(
                environment="production",
                report_date=(now - timedelta(days=1)).date(),
                trigger_type=models.DailyReportScheduleRunTrigger.MANUAL,
                status=models.DailyReportScheduleRunStatus.SUCCEEDED,
                scheduled_for=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=1),
            )
        )
        for stopped, heartbeat_age in [
            (False, age),
            (False, timedelta(seconds=60)),
            (True, timedelta(seconds=1)),
        ]:
            session.add(
                models.WorkerHeartbeatRecord(
                    instance_id=uuid4(),
                    started_at=now - timedelta(minutes=5),
                    last_heartbeat_at=now - heartbeat_age,
                    stopped_at=now if stopped else None,
                    version="test",
                )
            )
        session.add(
            SchedulerHeartbeatRecord(
                scheduler_name="daily-report",
                instance_id=uuid4(),
                started_at=now - age,
                last_heartbeat_at=now - age,
                version="test",
            )
        )
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            payload = (await client.get(ROOT)).json()
            assert payload["worker"]["status"] == ("ONLINE" if fresh else "OFFLINE")
            assert payload["worker"]["online_instances"] == (1 if fresh else 0)
            assert payload["scheduler"]["status"] == ("ONLINE" if fresh else "OFFLINE")
            assert payload["scheduler"]["last_success_at"] is not None

    asyncio.run(exercise())


def test_snapshot_database_error_returns_safe_unavailable_response(
    settings: Settings,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap(session_factory)
    app = create_app(settings, session_factory)

    def unavailable(*_: object) -> None:
        raise SQLAlchemyError("private-database-connection-token")

    monkeypatch.setattr("dagsentry.routes.operations._snapshot", unavailable)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            response = await client.get(ROOT)
            assert response.status_code == 503
            assert "private-database" not in response.text
            assert "no-store" in response.headers["cache-control"]
            assert "queue" not in response.json()

    asyncio.run(exercise())


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.OPERATOR])
def test_operational_snapshot_requires_admin(
    settings: Settings,
    session_factory: SessionFactory,
    role: UserRole,
) -> None:
    bootstrap(session_factory)
    with session_factory.begin() as session:
        user = session.scalar(select(UserRecord))
        assert user is not None
        user.role = role
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            assert (await client.get(ROOT)).status_code == 401
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            assert (await client.get(ROOT)).status_code == 403

    asyncio.run(exercise())
