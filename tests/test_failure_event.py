from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dagsentry.domain.failure_event import (
    CollectionSource,
    FailureEventCreate,
    FailureEventIdentity,
    FailureState,
    canonical_event_identity,
    make_event_key,
)


def identity(**changes: object) -> FailureEventIdentity:
    values: dict[str, object] = {
        "environment": "production",
        "dag_id": "daily_orders",
        "dag_run_id": "scheduled__2026-08-09T00:00:00+00:00",
        "task_id": "load_orders",
        "map_index": -1,
        "try_number": 2,
    }
    values.update(changes)
    return FailureEventIdentity.model_validate(values)


def test_event_key_has_stable_canonical_input() -> None:
    assert canonical_event_identity(identity()).decode() == (
        '{"dag_id":"daily_orders",'
        '"dag_run_id":"scheduled__2026-08-09T00:00:00+00:00",'
        '"environment":"production",'
        '"event_key_version":1,'
        '"map_index":-1,'
        '"task_id":"load_orders",'
        '"try_number":2}'
    )
    assert make_event_key(identity()) == (
        "3ad700f80dad860dda68fb54927bc27532da48dd26b34eeab4ae9e65acbf0b5e"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "staging"),
        ("dag_id", "weekly_orders"),
        ("dag_run_id", "manual__2026-08-09T00:00:00+00:00"),
        ("task_id", "validate_orders"),
        ("map_index", 0),
        ("try_number", 3),
    ],
)
def test_every_identity_field_changes_event_key(field: str, value: object) -> None:
    assert make_event_key(identity()) != make_event_key(identity(**{field: value}))


def test_non_identity_fields_do_not_change_event_key() -> None:
    base = {
        **identity().model_dump(),
        "source": CollectionSource.LISTENER,
        "state": FailureState.FAILED,
        "observed_at": datetime(2026, 8, 9, tzinfo=UTC),
    }
    first = FailureEventCreate.model_validate(base)
    second = FailureEventCreate.model_validate(
        {**base, "operator_type": "PythonOperator", "observed_at": "2026-08-09T01:00:00+00:00"}
    )

    assert make_event_key(first.identity()) == make_event_key(second.identity())


def test_timestamp_is_required_to_be_timezone_aware_and_normalized_to_utc() -> None:
    values = {
        **identity().model_dump(),
        "source": CollectionSource.LISTENER,
        "state": FailureState.FAILED,
        "observed_at": "2026-08-09T09:00:00+09:00",
    }

    event = FailureEventCreate.model_validate(values)

    assert event.observed_at == datetime(2026, 8, 9, tzinfo=UTC)

    with pytest.raises(ValidationError, match="must include a timezone"):
        FailureEventCreate.model_validate({**values, "observed_at": "2026-08-09T00:00:00"})


def test_retry_callback_requires_up_for_retry_state() -> None:
    with pytest.raises(ValidationError, match="must have state UP_FOR_RETRY"):
        FailureEventCreate.model_validate(
            {
                **identity().model_dump(),
                "source": CollectionSource.RETRY_CALLBACK,
                "state": FailureState.FAILED,
                "observed_at": "2026-08-09T00:00:00Z",
            }
        )


def test_listener_requires_failed_state() -> None:
    with pytest.raises(ValidationError, match="must have state FAILED"):
        FailureEventCreate.model_validate(
            {
                **identity().model_dump(),
                "source": CollectionSource.LISTENER,
                "state": FailureState.UP_FOR_RETRY,
                "observed_at": "2026-08-09T00:00:00Z",
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "Production"},
        {"dag_id": " daily_orders"},
        {"map_index": -2},
        {"try_number": 0},
    ],
)
def test_invalid_identity_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        identity(**changes)
