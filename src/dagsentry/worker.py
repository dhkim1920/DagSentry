"""Reliable processing of diagnosis outbox jobs."""

from __future__ import annotations

import json
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from types import FrameType
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dagsentry.db import SessionFactory
from dagsentry.metrics import increment_counter
from dagsentry.models import DiagnosisOutboxRecord, OutboxStatus

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedJob:
    """An outbox job owned by one worker for one processing attempt."""

    outbox_id: UUID
    failure_event_id: UUID
    attempt_count: int


@dataclass(frozen=True)
class WorkerOptions:
    """Bounded retry and polling behavior for one worker process."""

    max_attempts: int = 5
    backoff_base_seconds: float = 5.0
    backoff_max_seconds: float = 300.0
    poll_interval_seconds: float = 1.0
    stale_lock_timeout_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must not be negative")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be at least backoff_base_seconds")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.stale_lock_timeout_seconds <= 0:
            raise ValueError("stale_lock_timeout_seconds must be positive")


class DiagnosisProcessingError(RuntimeError):
    """A diagnosis failure with an explicit retry policy and processing stage."""

    retryable = True

    def __init__(self, message: str, *, stage: str = "diagnosis") -> None:
        super().__init__(message)
        self.stage = stage


class PermanentDiagnosisError(DiagnosisProcessingError):
    """A diagnosis failure that cannot succeed when retried unchanged."""

    retryable = False


DiagnosisHandler = Callable[[UUID], None]
Clock = Callable[[], datetime]


class DiagnosisWorker:
    """Claim and process diagnosis jobs with bounded retries."""

    def __init__(
        self,
        session_factory: SessionFactory,
        handler: DiagnosisHandler,
        worker_id: str,
        *,
        options: WorkerOptions | None = None,
        clock: Clock = utc_now,
    ) -> None:
        if not worker_id or len(worker_id) > 250:
            raise ValueError("worker_id must contain 1 to 250 characters")
        self.session_factory = session_factory
        self.handler = handler
        self.worker_id = worker_id
        self.options = options or WorkerOptions()
        self.clock = clock
        self.stop_event = Event()

    def request_stop(self, _signal: int | None = None, _frame: FrameType | None = None) -> None:
        """Stop claiming new jobs after the current job is safely finalized."""
        self.stop_event.set()

    def install_signal_handlers(self) -> None:
        """Request graceful shutdown on the normal process termination signals."""
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

    def run(self) -> None:
        """Process available work until graceful shutdown is requested."""
        while not self.stop_event.is_set():
            try:
                processed = self.process_one()
            except SQLAlchemyError:
                logger.exception("worker database operation failed")
                processed = False
            if not processed:
                self.stop_event.wait(self.options.poll_interval_seconds)

    def process_one(self) -> bool:
        """Process at most one job, returning whether a job was claimed."""
        job = self.claim_one()
        if job is None:
            return False

        try:
            self.handler(job.failure_event_id)
        except Exception as error:  # noqa: BLE001 - the outbox must record handler failures
            self._record_failure(job, error)
        else:
            self._record_success(job)
        return True

    def claim_one(self) -> ClaimedJob | None:
        """Atomically claim one ready job without waiting on another worker."""
        now = self.clock()
        stale_before = now - timedelta(seconds=self.options.stale_lock_timeout_seconds)

        with self.session_factory() as session, session.begin():
            expired = session.execute(
                update(DiagnosisOutboxRecord)
                .where(
                    DiagnosisOutboxRecord.status == OutboxStatus.PROCESSING,
                    DiagnosisOutboxRecord.locked_at <= stale_before,
                    DiagnosisOutboxRecord.attempt_count >= self.options.max_attempts,
                )
                .values(
                    status=OutboxStatus.DEAD,
                    locked_at=None,
                    locked_by=None,
                    last_error=_error_json(
                        stage="worker_recovery",
                        category="attempts_exhausted",
                        exception_type="StaleWorkerLock",
                        message="Worker lock expired after the final processing attempt",
                    ),
                    updated_at=now,
                )
            )
            expired_count = int(getattr(expired, "rowcount", 0))
            if expired_count > 0:
                increment_counter(
                    session,
                    "dagsentry_worker_jobs_total",
                    "dead",
                    amount=expired_count,
                    now=now,
                )
            record = session.scalar(
                select(DiagnosisOutboxRecord)
                .where(
                    or_(
                        and_(
                            DiagnosisOutboxRecord.status == OutboxStatus.PENDING,
                            DiagnosisOutboxRecord.available_at <= now,
                        ),
                        and_(
                            DiagnosisOutboxRecord.status == OutboxStatus.PROCESSING,
                            DiagnosisOutboxRecord.locked_at <= stale_before,
                        ),
                    ),
                    DiagnosisOutboxRecord.attempt_count < self.options.max_attempts,
                )
                .order_by(
                    DiagnosisOutboxRecord.available_at,
                    DiagnosisOutboxRecord.created_at,
                    DiagnosisOutboxRecord.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None

            stale_lock_reclaimed = record.status == OutboxStatus.PROCESSING
            record.status = OutboxStatus.PROCESSING
            record.attempt_count += 1
            record.locked_at = now
            record.locked_by = self.worker_id
            if stale_lock_reclaimed:
                record.last_error = _error_json(
                    stage="worker_recovery",
                    category="stale_lock_reclaimed",
                    exception_type="StaleWorkerLock",
                    message="Worker lock expired and ownership was reclaimed",
                )
                increment_counter(
                    session,
                    "dagsentry_worker_jobs_total",
                    "stale_reclaimed",
                    now=now,
                )
            record.updated_at = now
            return ClaimedJob(record.id, record.failure_event_id, record.attempt_count)

    def _record_success(self, job: ClaimedJob) -> None:
        now = self.clock()
        with self.session_factory() as session, session.begin():
            record = self._locked_owned_job(session, job)
            if record is None:
                logger.warning("worker lost ownership before recording success")
                return
            record.status = OutboxStatus.COMPLETED
            record.locked_at = None
            record.locked_by = None
            record.last_error = None
            record.updated_at = now
            increment_counter(
                session,
                "dagsentry_worker_jobs_total",
                "completed",
                now=now,
            )

    def _record_failure(self, job: ClaimedJob, error: Exception) -> None:
        now = self.clock()
        retryable = not isinstance(error, DiagnosisProcessingError) or error.retryable
        stage = error.stage if isinstance(error, DiagnosisProcessingError) else "diagnosis"
        category = "retryable" if retryable else "permanent"

        with self.session_factory() as session, session.begin():
            record = self._locked_owned_job(session, job)
            if record is None:
                logger.warning("worker lost ownership before recording failure")
                return

            exhausted = record.attempt_count >= self.options.max_attempts
            if retryable and not exhausted:
                record.status = OutboxStatus.PENDING
                record.available_at = now + self._backoff(record.attempt_count)
            else:
                record.status = OutboxStatus.DEAD
            record.locked_at = None
            record.locked_by = None
            record.last_error = _error_json(
                stage=stage,
                category="attempts_exhausted" if exhausted and retryable else category,
                exception_type=type(error).__name__,
                message=str(error),
            )
            record.updated_at = now
            increment_counter(
                session,
                "dagsentry_worker_jobs_total",
                "retry" if retryable and not exhausted else "dead",
                now=now,
            )

    def _locked_owned_job(self, session: Session, job: ClaimedJob) -> DiagnosisOutboxRecord | None:
        # Session is intentionally kept local to each short state-transition transaction.
        record = session.scalar(
            select(DiagnosisOutboxRecord)
            .where(
                DiagnosisOutboxRecord.id == job.outbox_id,
                DiagnosisOutboxRecord.status == OutboxStatus.PROCESSING,
                DiagnosisOutboxRecord.locked_by == self.worker_id,
                DiagnosisOutboxRecord.attempt_count == job.attempt_count,
            )
            .with_for_update()
        )
        return record

    def _backoff(self, attempt_count: int) -> timedelta:
        seconds = min(
            self.options.backoff_max_seconds,
            self.options.backoff_base_seconds * (2 ** (attempt_count - 1)),
        )
        return timedelta(seconds=seconds)


def _error_json(
    *,
    stage: str,
    category: str,
    exception_type: str,
    message: str,
) -> str:
    return json.dumps(
        {
            "stage": stage,
            "category": category,
            "exception_type": exception_type,
            "message": message[:2000],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
