from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.identity import UserRole
from dagsentry.models import AdminAuditEventRecord, DiagnosisOutboxRecord, OutboxStatus, UserRecord
from tests.test_admin_api import ADMIN_PASSWORD, bootstrap, csrf_headers, login
from tests.test_worker import NOW, add_job, get_outbox

ROOT = "/api/v1/admin/worker-jobs"


def dead_job(session_factory: SessionFactory, task: str = "failed_task") -> DiagnosisOutboxRecord:
    failure_id = add_job(session_factory, task_id=task)
    with session_factory.begin() as session:
        record = session.scalar(
            select(DiagnosisOutboxRecord).where(
                DiagnosisOutboxRecord.failure_event_id == failure_id
            )
        )
        assert record is not None
        record.status = OutboxStatus.DEAD
        record.attempt_count = 5
        record.locked_at = NOW
        record.locked_by = "old-worker"
        record.updated_at = NOW
        record.last_error = json.dumps(
            {
                "category": "attempts_exhausted",
                "stage": "notification",
                "exception_type": "RetryableDiagnosisError",
                "message": "private-error-token",
            }
        )
    return get_outbox(session_factory, failure_id)


def test_dead_jobs_are_bounded_filtered_and_do_not_expose_raw_errors(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    first = dead_job(session_factory, "first")
    second = dead_job(session_factory, "second")
    add_job(session_factory, task_id="pending")
    with session_factory.begin() as session:
        record = session.get(DiagnosisOutboxRecord, second.id)
        assert record is not None
        record.updated_at = NOW + timedelta(seconds=1)
        record.last_error = "private-legacy-error-token"
    app = create_app(settings, session_factory)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            response = await client.get(ROOT, params={"limit": 1, "environment": "test"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert len(payload["items"]) == 1
            job = payload["items"][0]
            assert job["outbox_id"] == str(first.id)
            assert job["environment"] == "test"
            assert job["dag_id"] == "worker_test"
            assert job["task_id"] == "first"
            assert job["attempt_count"] == 5
            assert job["error_category"] == "attempts_exhausted"
            assert job["error_stage"] == "notification"
            assert job["error_type"] == "RetryableDiagnosisError"
            assert "private" not in response.text
            assert "last_error" not in job
            page = await client.get(ROOT, params={"limit": 1, "offset": 1})
            assert page.json()["items"][0]["outbox_id"] == str(second.id)
            assert page.json()["items"][0]["error_category"] is None
            assert "private" not in page.text
            assert (await client.get(ROOT, params={"environment": "absent"})).json()["total"] == 0
            for params in [{"limit": 0}, {"limit": 201}, {"offset": -1}]:
                assert (await client.get(ROOT, params=params)).status_code == 422

    asyncio.run(exercise())


def test_requeue_requires_csrf_is_audited_and_rejects_duplicates(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    job = dead_job(session_factory)
    app = create_app(settings, session_factory)
    correlation = str(uuid4())

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await login(client, "admin@example.com", ADMIN_PASSWORD)
            target = f"{ROOT}/{job.id}/requeue"
            assert (await client.post(target)).status_code == 403
            assert get_outbox(session_factory, job.failure_event_id).status == OutboxStatus.DEAD
            headers = {**csrf_headers(client), "X-Correlation-ID": correlation}
            response = await client.post(target, headers=headers)
            assert response.status_code == 202
            assert response.json()["outbox_id"] == str(job.id)
            assert "private" not in response.text
            assert (await client.post(target, headers=headers)).status_code == 409
            assert (
                await client.post(f"{ROOT}/{uuid4()}/requeue", headers=headers)
            ).status_code == 404
            assert (await client.get(ROOT)).json()["total"] == 0

    asyncio.run(exercise())
    updated = get_outbox(session_factory, job.failure_event_id)
    assert updated.status == OutboxStatus.PENDING
    assert updated.attempt_count == 0
    assert updated.locked_at is None and updated.locked_by is None and updated.last_error is None
    with session_factory() as session:
        audits = session.scalars(
            select(AdminAuditEventRecord).where(
                AdminAuditEventRecord.action == "worker_job.requeued"
            )
        ).all()
        assert len(audits) == 1
        audit = audits[0]
        assert audit.target_id == job.id
        assert audit.actor_user_id is not None
        assert audit.correlation_id == correlation
        assert audit.change_summary == {
            "failure_event_id": str(job.failure_event_id),
            "previous_attempt_count": 5,
            "previous_status": "DEAD",
            "status": "PENDING",
        }


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.OPERATOR])
def test_non_admin_sessions_cannot_inspect_or_requeue_jobs(
    settings: Settings,
    session_factory: SessionFactory,
    role: UserRole,
) -> None:
    bootstrap(session_factory)
    job = dead_job(session_factory)
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
            assert (
                await client.post(f"{ROOT}/{job.id}/requeue", headers=csrf_headers(client))
            ).status_code == 403

    asyncio.run(exercise())
    assert get_outbox(session_factory, job.failure_event_id).status == OutboxStatus.DEAD


def test_requeue_rolls_back_if_audit_cannot_be_saved(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    bootstrap(session_factory)
    job = dead_job(session_factory)

    def reject_audit(*_: object) -> None:
        raise RuntimeError("audit storage unavailable")

    event.listen(AdminAuditEventRecord, "before_insert", reject_audit)
    try:
        app = create_app(settings, session_factory)

        async def exercise() -> None:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                await login(client, "admin@example.com", ADMIN_PASSWORD)
                response = await client.post(
                    f"{ROOT}/{job.id}/requeue", headers=csrf_headers(client)
                )
                assert response.status_code == 500

        asyncio.run(exercise())
    finally:
        event.remove(AdminAuditEventRecord, "before_insert", reject_audit)
    assert get_outbox(session_factory, job.failure_event_id).status == OutboxStatus.DEAD
