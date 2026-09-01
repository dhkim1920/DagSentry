"""Transactional ingestion of Failure Events."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from dagsentry.domain.failure_event import EVENT_KEY_VERSION, FailureEventCreate, make_event_key
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an idempotent Failure Event ingestion."""

    failure_event_id: UUID
    event_key: str
    created: bool


def ingest_failure_event(session: Session, event: FailureEventCreate) -> IngestResult:
    """Atomically persist a Failure Event and its diagnosis outbox job."""
    event_key = make_event_key(event.identity())
    failure_event_id = uuid4()
    values = {
        "id": failure_event_id,
        "event_key": event_key,
        "event_key_version": EVENT_KEY_VERSION,
        "environment": event.environment,
        "dag_id": event.dag_id,
        "dag_run_id": event.dag_run_id,
        "task_id": event.task_id,
        "map_index": event.map_index,
        "try_number": event.try_number,
        "source": event.source,
        "state": event.state,
        "observed_at": event.observed_at,
        "operator_type": event.operator_type,
    }

    with session.begin():
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = (
                postgresql_insert(FailureEventRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["event_key"])
                .returning(FailureEventRecord.id)
            )
        elif dialect_name == "sqlite":
            statement = (
                sqlite_insert(FailureEventRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["event_key"])
                .returning(FailureEventRecord.id)
            )
        else:  # pragma: no cover - only configured dialects are supported
            raise RuntimeError(f"Unsupported database dialect: {dialect_name}")

        inserted_id = session.scalar(statement)
        if inserted_id is None:
            existing_id = session.scalar(
                select(FailureEventRecord.id).where(FailureEventRecord.event_key == event_key)
            )
            if existing_id is None:  # pragma: no cover - database invariant
                raise RuntimeError("Conflicting Failure Event was not found")
            return IngestResult(existing_id, event_key, created=False)

        session.add(DiagnosisOutboxRecord(failure_event_id=failure_event_id))

    return IngestResult(failure_event_id, event_key, created=True)
