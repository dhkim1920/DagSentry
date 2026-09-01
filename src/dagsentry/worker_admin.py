"""Operational inspection and manual requeue commands for Diagnosis jobs."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.models import DiagnosisOutboxRecord, OutboxStatus


class WorkerJobNotFoundError(ValueError):
    """No Diagnosis Outbox job matched an explicit operator target."""


class WorkerJobStateError(ValueError):
    """The requested operation is invalid for the job's current state."""


@dataclass(frozen=True)
class DeadWorkerJob:
    """Bounded operational view of one exhausted job."""

    outbox_id: UUID
    failure_event_id: UUID
    attempt_count: int
    last_error: str | None
    updated_at: datetime


@dataclass(frozen=True)
class RequeuedWorkerJob:
    """Result of manually returning one DEAD job to PENDING."""

    outbox_id: UUID
    failure_event_id: UUID
    available_at: datetime


def list_dead_jobs(session: Session, *, limit: int = 100) -> list[DeadWorkerJob]:
    """List a bounded set of DEAD jobs from oldest update first."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    records = session.scalars(
        select(DiagnosisOutboxRecord)
        .where(DiagnosisOutboxRecord.status == OutboxStatus.DEAD)
        .order_by(DiagnosisOutboxRecord.updated_at, DiagnosisOutboxRecord.id)
        .limit(limit)
    ).all()
    return [
        DeadWorkerJob(
            outbox_id=record.id,
            failure_event_id=record.failure_event_id,
            attempt_count=record.attempt_count,
            last_error=record.last_error,
            updated_at=_as_utc(record.updated_at),
        )
        for record in records
    ]


def requeue_dead_job(
    session: Session,
    *,
    outbox_id: UUID | None = None,
    failure_event_id: UUID | None = None,
    now: datetime | None = None,
) -> RequeuedWorkerJob:
    """Explicitly reset one DEAD job for a fresh bounded processing cycle."""
    if (outbox_id is None) == (failure_event_id is None):
        raise ValueError("exactly one outbox_id or failure_event_id is required")
    with session.begin():
        statement = select(DiagnosisOutboxRecord).with_for_update()
        if outbox_id is not None:
            statement = statement.where(DiagnosisOutboxRecord.id == outbox_id)
        else:
            statement = statement.where(DiagnosisOutboxRecord.failure_event_id == failure_event_id)
        record = session.scalar(statement)
        if record is None:
            raise WorkerJobNotFoundError("Diagnosis Outbox job was not found")
        if record.status != OutboxStatus.DEAD:
            raise WorkerJobStateError(f"Only DEAD jobs can be requeued; status is {record.status}")
        available_at = _as_utc(now or datetime.now(UTC))
        record.status = OutboxStatus.PENDING
        record.attempt_count = 0
        record.available_at = available_at
        record.locked_at = None
        record.locked_by = None
        record.last_error = None
        record.updated_at = available_at
        return RequeuedWorkerJob(
            outbox_id=record.id,
            failure_event_id=record.failure_event_id,
            available_at=available_at,
        )


def main(argv: Sequence[str] | None = None) -> None:
    """Inspect or requeue Diagnosis Outbox jobs using JSON output."""
    parser = argparse.ArgumentParser(prog="dagsentry-worker-jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list-dead", help="List exhausted jobs")
    list_parser.add_argument("--limit", type=int, default=100)
    requeue_parser = commands.add_parser("requeue", help="Return one DEAD job to PENDING")
    target = requeue_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--outbox-id", type=UUID)
    target.add_argument("--failure-event-id", type=UUID)
    arguments = parser.parse_args(argv)

    session_factory = create_session_factory(get_settings().database_url)
    with session_factory() as session:
        if arguments.command == "list-dead":
            for job in list_dead_jobs(session, limit=arguments.limit):
                print(json.dumps(_json_value(asdict(job)), sort_keys=True))
            return
        result = requeue_dead_job(
            session,
            outbox_id=arguments.outbox_id,
            failure_event_id=arguments.failure_event_id,
        )
        print(json.dumps(_json_value(asdict(result)), sort_keys=True))


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
