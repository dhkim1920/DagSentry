import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import (
    DiagnosisOutboxRecord,
    OperationalMetricCounterRecord,
    OutboxStatus,
)
from dagsentry.worker import (
    DiagnosisProcessingError,
    DiagnosisWorker,
    PermanentDiagnosisError,
    WorkerOptions,
)
from dagsentry.worker_admin import (
    WorkerJobNotFoundError,
    WorkerJobStateError,
    list_dead_jobs,
    requeue_dead_job,
)

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def add_job(session_factory: SessionFactory, *, task_id: str = "task") -> UUID:
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="test",
                dag_id="worker_test",
                dag_run_id="manual__2026-08-09T12:00:00+00:00",
                task_id=task_id,
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=NOW,
            ),
        )
        with session.begin():
            record = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert record is not None
            record.available_at = NOW
    return result.failure_event_id


def get_outbox(session_factory: SessionFactory, failure_event_id: UUID) -> DiagnosisOutboxRecord:
    with session_factory() as session:
        record = session.scalar(
            select(DiagnosisOutboxRecord).where(
                DiagnosisOutboxRecord.failure_event_id == failure_event_id
            )
        )
        assert record is not None
        session.expunge(record)
        return record


def test_worker_claims_and_completes_one_job(session_factory: SessionFactory) -> None:
    failure_event_id = add_job(session_factory)
    handled: list[UUID] = []
    worker = DiagnosisWorker(session_factory, handled.append, "worker-1", clock=lambda: NOW)

    assert worker.process_one() is True

    record = get_outbox(session_factory, failure_event_id)
    assert handled == [failure_event_id]
    assert record.status == OutboxStatus.COMPLETED
    assert record.attempt_count == 1
    assert record.locked_at is None
    assert record.locked_by is None
    assert record.last_error is None
    assert worker.process_one() is False


def test_retryable_failure_uses_exponential_backoff(session_factory: SessionFactory) -> None:
    failure_event_id = add_job(session_factory)

    def fail(_: UUID) -> None:
        raise DiagnosisProcessingError("Airflow API timed out", stage="log_collection")

    worker = DiagnosisWorker(session_factory, fail, "worker-1", clock=lambda: NOW)

    assert worker.process_one() is True

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.PENDING
    assert record.attempt_count == 1
    assert record.available_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=5)
    assert json.loads(record.last_error or "") == {
        "category": "retryable",
        "exception_type": "DiagnosisProcessingError",
        "message": "Diagnosis processing failed",
        "stage": "log_collection",
    }
    with session_factory() as session:
        metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_worker_jobs_total", "retry"),
        )
        assert metric is not None
        assert metric.value == 1


def test_retryable_failure_becomes_dead_at_max_attempts(
    session_factory: SessionFactory,
) -> None:
    failure_event_id = add_job(session_factory)

    def fail(_: UUID) -> None:
        raise RuntimeError("temporary failure")

    worker = DiagnosisWorker(
        session_factory,
        fail,
        "worker-1",
        options=WorkerOptions(
            max_attempts=2,
            backoff_base_seconds=0,
            backoff_max_seconds=0,
        ),
        clock=lambda: NOW,
    )

    assert worker.process_one() is True
    assert worker.process_one() is True

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.DEAD
    assert record.attempt_count == 2
    assert json.loads(record.last_error or "")["category"] == "attempts_exhausted"
    assert worker.process_one() is False


def test_permanent_failure_is_not_retried(session_factory: SessionFactory) -> None:
    failure_event_id = add_job(session_factory)

    def fail(_: UUID) -> None:
        raise PermanentDiagnosisError("invalid event", stage="validation")

    worker = DiagnosisWorker(session_factory, fail, "worker-1", clock=lambda: NOW)

    assert worker.process_one() is True

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.DEAD
    assert record.attempt_count == 1
    assert json.loads(record.last_error or "")["category"] == "permanent"


def test_stale_processing_job_is_reclaimed_and_completed(
    session_factory: SessionFactory,
) -> None:
    failure_event_id = add_job(session_factory)
    first_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-1",
        clock=lambda: NOW,
    )
    assert first_worker.claim_one() is not None

    handled: list[UUID] = []
    second_worker = DiagnosisWorker(
        session_factory,
        handled.append,
        "worker-2",
        clock=lambda: NOW + timedelta(seconds=901),
    )
    assert second_worker.process_one() is True

    record = get_outbox(session_factory, failure_event_id)
    assert handled == [failure_event_id]
    assert record.status == OutboxStatus.COMPLETED
    assert record.attempt_count == 2
    assert record.locked_at is None
    assert record.locked_by is None


def test_processing_job_is_not_reclaimed_before_timeout(
    session_factory: SessionFactory,
) -> None:
    failure_event_id = add_job(session_factory)
    first_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-1",
        clock=lambda: NOW,
    )
    assert first_worker.claim_one() is not None

    second_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-2",
        clock=lambda: NOW + timedelta(seconds=899),
    )
    assert second_worker.process_one() is False

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.PROCESSING
    assert record.attempt_count == 1
    assert record.locked_by == "worker-1"


def test_stale_final_attempt_becomes_dead(session_factory: SessionFactory) -> None:
    failure_event_id = add_job(session_factory)
    options = WorkerOptions(max_attempts=1)
    first_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-1",
        options=options,
        clock=lambda: NOW,
    )
    assert first_worker.claim_one() is not None

    second_worker = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-2",
        options=options,
        clock=lambda: NOW + timedelta(seconds=901),
    )
    assert second_worker.process_one() is False

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.DEAD
    assert record.attempt_count == 1
    assert record.locked_at is None
    assert record.locked_by is None
    assert json.loads(record.last_error or "") == {
        "category": "attempts_exhausted",
        "exception_type": "StaleWorkerLock",
        "message": "Worker lock expired after the final processing attempt",
        "stage": "worker_recovery",
    }


def test_poison_job_does_not_block_later_job(session_factory: SessionFactory) -> None:
    poison_id = add_job(session_factory, task_id="poison")
    healthy_id = add_job(session_factory, task_id="healthy")
    handled: list[UUID] = []

    def handle(failure_event_id: UUID) -> None:
        if failure_event_id == poison_id:
            raise RuntimeError("poison job")
        handled.append(failure_event_id)

    worker = DiagnosisWorker(
        session_factory,
        handle,
        "worker-1",
        options=WorkerOptions(
            max_attempts=2,
            backoff_base_seconds=0,
            backoff_max_seconds=0,
        ),
        clock=lambda: NOW,
    )

    assert worker.process_one() is True
    assert worker.process_one() is True
    assert worker.process_one() is True

    assert get_outbox(session_factory, poison_id).status == OutboxStatus.DEAD
    assert get_outbox(session_factory, healthy_id).status == OutboxStatus.COMPLETED
    assert handled == [healthy_id]


def test_dead_job_can_be_listed_requeued_and_processed(
    session_factory: SessionFactory,
) -> None:
    failure_event_id = add_job(session_factory)

    def fail(_: UUID) -> None:
        raise PermanentDiagnosisError("invalid event")

    worker = DiagnosisWorker(session_factory, fail, "worker-1", clock=lambda: NOW)
    assert worker.process_one() is True
    dead_record = get_outbox(session_factory, failure_event_id)

    with session_factory() as session:
        jobs = list_dead_jobs(session)
    assert [job.outbox_id for job in jobs] == [dead_record.id]

    requeued_at = NOW + timedelta(seconds=1)
    with session_factory() as session:
        result = requeue_dead_job(
            session,
            failure_event_id=failure_event_id,
            now=requeued_at,
        )
    assert result.outbox_id == dead_record.id

    record = get_outbox(session_factory, failure_event_id)
    assert record.status == OutboxStatus.PENDING
    assert record.attempt_count == 0
    assert record.last_error is None

    recovered = DiagnosisWorker(
        session_factory,
        lambda _: None,
        "worker-2",
        clock=lambda: requeued_at,
    )
    assert recovered.process_one() is True
    assert get_outbox(session_factory, failure_event_id).status == OutboxStatus.COMPLETED


def test_requeue_rejects_non_dead_and_unknown_jobs(
    session_factory: SessionFactory,
) -> None:
    failure_event_id = add_job(session_factory)

    with session_factory() as session:
        with pytest.raises(WorkerJobStateError, match="Only DEAD jobs"):
            requeue_dead_job(session, failure_event_id=failure_event_id, now=NOW)

    with session_factory() as session:
        with pytest.raises(WorkerJobNotFoundError, match="was not found"):
            requeue_dead_job(session, failure_event_id=uuid4(), now=NOW)


def test_worker_options_require_positive_stale_lock_timeout() -> None:
    with pytest.raises(ValueError, match="stale_lock_timeout_seconds must be positive"):
        WorkerOptions(stale_lock_timeout_seconds=0)


def test_shutdown_finishes_current_job_without_claiming_another(
    session_factory: SessionFactory,
) -> None:
    first_id = add_job(session_factory, task_id="first")
    second_id = add_job(session_factory, task_id="second")
    handled: list[UUID] = []
    worker: DiagnosisWorker

    def handle(failure_event_id: UUID) -> None:
        handled.append(failure_event_id)
        worker.request_stop()

    worker = DiagnosisWorker(session_factory, handle, "worker-1", clock=lambda: NOW)
    worker.run()

    records = {
        record.failure_event_id: record
        for record in (
            get_outbox(session_factory, first_id),
            get_outbox(session_factory, second_id),
        )
    }
    assert len(handled) == 1
    assert records[handled[0]].status == OutboxStatus.COMPLETED
    remaining_id = second_id if handled[0] == first_id else first_id
    assert records[remaining_id].status == OutboxStatus.PENDING

    restarted = DiagnosisWorker(
        session_factory,
        handled.append,
        "worker-2",
        clock=lambda: NOW,
    )
    assert restarted.process_one() is True
    assert get_outbox(session_factory, remaining_id).status == OutboxStatus.COMPLETED


def test_worker_loop_continues_after_temporary_database_error(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class DatabaseRecoversWorker(DiagnosisWorker):
        calls = 0

        def process_one(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise SQLAlchemyError("database-password-disposable-secret")
            self.request_stop()
            return False

    worker = DatabaseRecoversWorker(
        session_factory,
        lambda _: None,
        "worker-1",
        options=WorkerOptions(poll_interval_seconds=0.001),
    )

    worker.run()

    assert worker.calls == 2
    assert "database-password-disposable-secret" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


def test_worker_retry_and_dead_logs_do_not_expose_exception_message(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure_id = add_job(session_factory)

    def fail(_: UUID) -> None:
        raise RuntimeError("https://example.test?token=disposable-private-token")

    worker = DiagnosisWorker(
        session_factory,
        fail,
        "test-worker",
        clock=lambda: NOW,
        options=WorkerOptions(max_attempts=2, backoff_base_seconds=0, backoff_max_seconds=0),
    )
    with caplog.at_level(logging.WARNING, logger="dagsentry.worker"):
        assert worker.process_one()
        assert worker.process_one()
    record = get_outbox(session_factory, failure_id)
    assert record.status == OutboxStatus.DEAD
    assert "disposable-private-token" not in caplog.text
    assert "disposable-private-token" not in (record.last_error or "")
    assert [item.levelno for item in caplog.records if item.name == "dagsentry.worker"] == [
        logging.WARNING,
        logging.ERROR,
    ]
    assert str(failure_id) in caplog.text
    assert "RuntimeError" in caplog.text
