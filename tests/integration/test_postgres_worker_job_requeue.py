from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from dagsentry.db import create_session_factory
from dagsentry.domain.identity import UserRole
from dagsentry.models import AdminAuditEventRecord, FailureEventRecord, OutboxStatus, UserRecord
from dagsentry.worker_admin import RequeuedWorkerJob, WorkerJobStateError, requeue_dead_job
from tests.test_worker import get_outbox
from tests.test_worker_job_api import dead_job

pytestmark = pytest.mark.integration


def test_concurrent_admin_requeue_changes_state_and_audits_only_once() -> None:
    url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    factory = create_session_factory(url)
    job = dead_job(factory, task=f"requeue-{uuid4().hex}")
    with factory.begin() as session:
        admin = UserRecord(
            email=f"requeue-{uuid4().hex}@example.com",
            display_name="Requeue test admin",
            role=UserRole.ADMIN,
            password_hash="unused-test-hash",
            password_changed_at=datetime.now(UTC),
        )
        session.add(admin)
        session.flush()
    barrier = Barrier(2)

    def requeue() -> RequeuedWorkerJob | None:
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                return requeue_dead_job(session, outbox_id=job.id, actor_user_id=admin.id)
            except WorkerJobStateError:
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(requeue) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        assert sum(result is not None for result in results) == 1
        updated = get_outbox(factory, job.failure_event_id)
        assert updated.status == OutboxStatus.PENDING
        assert updated.attempt_count == 0
        with factory() as session:
            audits = session.scalars(
                select(AdminAuditEventRecord).where(AdminAuditEventRecord.target_id == job.id)
            ).all()
            assert len(audits) == 1
            assert audits[0].actor_user_id == admin.id
            assert audits[0].change_summary["previous_attempt_count"] == 5
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AdminAuditEventRecord).where(AdminAuditEventRecord.target_id == job.id)
            )
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.id == job.failure_event_id)
            )
            session.execute(delete(UserRecord).where(UserRecord.id == admin.id))
