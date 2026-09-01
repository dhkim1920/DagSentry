from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def failure(
    *,
    try_number: int,
    source: CollectionSource,
    state: FailureState,
    map_index: int = -1,
) -> FailureEventCreate:
    return FailureEventCreate(
        environment="test",
        dag_id="collection_scenarios",
        dag_run_id="run",
        task_id="task",
        map_index=map_index,
        try_number=try_number,
        source=source,
        state=state,
        observed_at=NOW,
    )


def counts(session: Session) -> tuple[int, int]:
    return (
        session.scalar(select(func.count()).select_from(FailureEventRecord)) or 0,
        session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord)) or 0,
    )


def test_first_try_failure_followed_by_retry_success_creates_one_event(session: Session) -> None:
    ingest_failure_event(
        session,
        failure(
            try_number=1,
            source=CollectionSource.RETRY_CALLBACK,
            state=FailureState.UP_FOR_RETRY,
        ),
    )

    assert counts(session) == (1, 1)


def test_every_failed_try_creates_its_own_event(session: Session) -> None:
    for try_number in (1, 2):
        ingest_failure_event(
            session,
            failure(
                try_number=try_number,
                source=CollectionSource.RETRY_CALLBACK,
                state=FailureState.UP_FOR_RETRY,
            ),
        )
    ingest_failure_event(
        session,
        failure(
            try_number=3,
            source=CollectionSource.LISTENER,
            state=FailureState.FAILED,
        ),
    )

    assert counts(session) == (3, 3)


def test_callback_and_listener_observation_of_same_try_is_deduplicated(session: Session) -> None:
    callback = ingest_failure_event(
        session,
        failure(
            try_number=2,
            source=CollectionSource.RETRY_CALLBACK,
            state=FailureState.UP_FOR_RETRY,
        ),
    )
    listener = ingest_failure_event(
        session,
        failure(
            try_number=2,
            source=CollectionSource.LISTENER,
            state=FailureState.FAILED,
        ),
    )

    assert callback.failure_event_id == listener.failure_event_id
    assert listener.created is False
    assert counts(session) == (1, 1)


def test_mapped_task_failures_are_separated_by_map_index(session: Session) -> None:
    ids = {
        ingest_failure_event(
            session,
            failure(
                try_number=1,
                map_index=map_index,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
            ),
        ).failure_event_id
        for map_index in (0, 1, 2)
    }

    assert len(ids) == 3
    assert counts(session) == (3, 3)
