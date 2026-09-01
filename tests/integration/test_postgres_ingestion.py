import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, delete, func, select, text

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import create_session_factory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import IngestResult, ingest_failure_event
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord, OutboxStatus
from dagsentry.worker import ClaimedJob, DiagnosisProcessingError, DiagnosisWorker, WorkerOptions

pytestmark = pytest.mark.integration


def test_concurrent_duplicate_ingestion_creates_one_event_and_outbox() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    unique_task_id = f"concurrent_{uuid4().hex}"
    failure = FailureEventCreate(
        environment="test",
        dag_id="integration_test",
        dag_run_id=f"manual__{uuid4().hex}",
        task_id=unique_task_id,
        map_index=-1,
        try_number=1,
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
        observed_at=datetime.now(UTC),
    )
    barrier = Barrier(8)

    def ingest() -> IngestResult:
        with session_factory() as session:
            barrier.wait()
            return ingest_failure_event(session, failure)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: ingest(), range(8)))

        assert sum(result.created for result in results) == 1
        assert len({result.failure_event_id for result in results}) == 1

        with session_factory() as session:
            event_count = session.scalar(
                select(func.count())
                .select_from(FailureEventRecord)
                .where(FailureEventRecord.task_id == unique_task_id)
            )
            outbox_count = session.scalar(
                select(func.count())
                .select_from(DiagnosisOutboxRecord)
                .join(FailureEventRecord)
                .where(FailureEventRecord.task_id == unique_task_id)
            )
            assert event_count == 1
            assert outbox_count == 1
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.task_id == unique_task_id)
            )


def test_multiple_workers_claim_one_outbox_job_once() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    unique_task_id = f"worker_{uuid4().hex}"
    failure = FailureEventCreate(
        environment="test",
        dag_id="integration_test",
        dag_run_id=f"manual__{uuid4().hex}",
        task_id=unique_task_id,
        map_index=-1,
        try_number=1,
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
        observed_at=datetime.now(UTC),
    )
    with session_factory() as session:
        result = ingest_failure_event(session, failure)
    barrier = Barrier(8)

    def claim(index: int) -> ClaimedJob | None:
        worker = DiagnosisWorker(session_factory, lambda _: None, f"worker-{index}")
        barrier.wait()
        return worker.claim_one()

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(claim, range(8)))

        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert claimed[0].failure_event_id == result.failure_event_id

        with session_factory() as session:
            outbox = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert outbox is not None
            assert outbox.status == OutboxStatus.PROCESSING
            assert outbox.attempt_count == 1
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.task_id == unique_task_id)
            )


def test_concurrent_ingest_api_smoke() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    app = create_app(
        Settings(database_url=database_url, ingest_api_token=SecretStr("integration-token")),
        session_factory,
    )

    async def send_all() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                *(
                    client.post(
                        "/api/v1/failure-events",
                        headers={"X-DagSentry-Token": "integration-token"},
                        json={
                            "environment": "integration",
                            "dag_id": "api_smoke",
                            "dag_run_id": marker,
                            "task_id": f"task_{index}",
                            "map_index": -1,
                            "try_number": 1,
                            "source": "LISTENER",
                            "state": "FAILED",
                            "observed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    for index in range(20)
                )
            )

    try:
        responses = asyncio.run(send_all())

        assert all(response.status_code == 200 for response in responses)
        assert len({response.json()["failure_event_id"] for response in responses}) == 20
        with session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(FailureEventRecord)
                .where(FailureEventRecord.dag_run_id == marker)
            )
            assert count == 20
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )


def test_worker_recovers_from_mid_pipeline_exception_without_losing_event() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="integration",
                dag_id="worker_recovery",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
            ),
        )
    calls = 0

    def handler(_failure_event_id: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DiagnosisProcessingError("temporary log failure", stage="log_collection")

    worker = DiagnosisWorker(
        session_factory,
        handler,
        "recovery-worker",
        options=WorkerOptions(backoff_base_seconds=0, backoff_max_seconds=0),
    )
    try:
        assert worker.process_one() is True
        assert worker.process_one() is True

        with session_factory() as session:
            event = session.get(FailureEventRecord, result.failure_event_id)
            outbox = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert event is not None
            assert outbox is not None
            assert outbox.status == OutboxStatus.COMPLETED
            assert outbox.attempt_count == 2
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )


def test_worker_reclaims_job_after_claiming_process_is_lost() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="integration",
                dag_id="worker_stale_recovery",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
            ),
        )
    claim_time = datetime.now(UTC)
    stopped_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "stopped-worker",
        clock=lambda: claim_time,
    )
    handled: list[object] = []
    restarted_worker = DiagnosisWorker(
        session_factory,
        handled.append,
        "restarted-worker",
        clock=lambda: claim_time + timedelta(seconds=901),
    )

    try:
        assert stopped_worker.claim_one() is not None
        assert restarted_worker.process_one() is True

        with session_factory() as session:
            event = session.get(FailureEventRecord, result.failure_event_id)
            outbox = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert event is not None
            assert outbox is not None
            assert outbox.status == OutboxStatus.COMPLETED
            assert outbox.attempt_count == 2
            assert handled == [result.failure_event_id]
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )


def test_worker_loop_recovers_after_postgres_disconnect() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="integration",
                dag_id="worker_disconnect",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
            ),
        )

    victim_engine = create_engine(database_url, pool_pre_ping=True)
    admin_engine = create_engine(database_url)
    victim = victim_engine.connect()
    backend_pid = victim.scalar(text("SELECT pg_backend_pid()"))
    assert backend_pid is not None
    with admin_engine.begin() as connection:
        assert connection.scalar(
            text("SELECT pg_terminate_backend(:backend_pid)"),
            {"backend_pid": backend_pid},
        )

    class DisconnectingWorker(DiagnosisWorker):
        calls = 0

        def process_one(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                victim.execute(text("SELECT 1"))
            return super().process_one()

    worker: DisconnectingWorker

    def handle(_failure_event_id: object) -> None:
        worker.request_stop()

    worker = DisconnectingWorker(
        session_factory,
        handle,
        "disconnect-worker",
        options=WorkerOptions(poll_interval_seconds=0.001),
    )
    try:
        worker.run()

        assert worker.calls == 2
        with session_factory() as session:
            outbox = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert outbox is not None
            assert outbox.status == OutboxStatus.COMPLETED
    finally:
        victim.close()
        victim_engine.dispose()
        admin_engine.dispose()
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )
