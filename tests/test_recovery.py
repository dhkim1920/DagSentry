from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationErrorCategory,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import (
    IncidentFailureRecord,
    IncidentRecord,
    IncidentRecoveryNotificationRecord,
    IncidentStateTransitionRecord,
    OperationalMetricCounterRecord,
)
from dagsentry.recovery import (
    AirflowTaskStateClient,
    RecoveryChecker,
    RecoveryCheckerError,
    TaskInstanceReference,
    make_recovery_delivery_key,
)

NOW = datetime(2026, 8, 11, 15, tzinfo=UTC)


@dataclass
class StubTaskStateClient:
    states: dict[TaskInstanceReference, str | None | Exception]
    calls: list[TaskInstanceReference] = field(default_factory=list)

    def get_state(self, reference: TaskInstanceReference) -> str | None:
        self.calls.append(reference)
        state = self.states[reference]
        if isinstance(state, Exception):
            raise state
        return state


@dataclass
class StubRecoveryNotificationProvider:
    name: str = "stub"
    fail: bool = False
    payloads: list[RecoveryNotificationPayload] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)

    def send(self, payload: RecoveryNotificationPayload, *, delivery_key: str) -> int:
        self.payloads.append(payload)
        self.keys.append(delivery_key)
        if self.fail:
            raise NotificationProviderError(
                "webhook unavailable",
                category=NotificationErrorCategory.UNAVAILABLE,
                retryable=True,
                response_status=503,
            )
        return 200


def create_incident(
    session_factory: SessionFactory,
    *,
    status: IncidentStatus = IncidentStatus.OPEN,
    task_count: int = 2,
) -> tuple[UUID, list[TaskInstanceReference]]:
    failure_ids: list[UUID] = []
    references: list[TaskInstanceReference] = []
    for index in range(task_count):
        reference = TaskInstanceReference(
            dag_id="orders",
            dag_run_id=f"scheduled__2026-08-11T{index:02d}:00:00+00:00",
            task_id="load",
            map_index=index - 1,
        )
        with session_factory() as session:
            failure_ids.append(
                ingest_failure_event(
                    session,
                    FailureEventCreate(
                        environment="production",
                        dag_id=reference.dag_id,
                        dag_run_id=reference.dag_run_id,
                        task_id=reference.task_id,
                        map_index=reference.map_index,
                        try_number=1,
                        source=CollectionSource.LISTENER,
                        state=FailureState.FAILED,
                        observed_at=NOW,
                    ),
                ).failure_event_id
            )
        references.append(reference)

    incident_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            IncidentRecord(
                id=incident_id,
                environment="production",
                dag_id="orders",
                task_id="load",
                error_signature_id=None,
                initial_failure_event_id=failure_ids[0],
                status=status,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            IncidentFailureRecord(incident_id=incident_id, failure_event_id=failure_id)
            for failure_id in failure_ids
        )
    return incident_id, references


@pytest.mark.parametrize(
    "initial_status",
    [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
)
def test_all_linked_successes_recover_once_and_notify_once(
    session_factory: SessionFactory,
    initial_status: IncidentStatus,
) -> None:
    incident_id, references = create_incident(session_factory, status=initial_status)
    airflow = StubTaskStateClient({reference: "SUCCESS" for reference in references})
    provider = StubRecoveryNotificationProvider()
    checker = RecoveryChecker(
        session_factory,
        airflow_client=airflow,
        notification_provider=provider,
        clock=lambda: NOW,
    )

    first = checker.run_once()
    repeated = checker.run_once()

    assert first.active_incidents_scanned == 1
    assert first.incidents_recovered == 1
    assert first.notifications_delivered == 1
    assert repeated.active_incidents_scanned == 0
    assert repeated.incidents_recovered == 0
    assert len(provider.payloads) == 1
    assert provider.payloads[0].incident_id == incident_id
    assert provider.payloads[0].incident_failure_count == 2
    assert provider.keys == [make_recovery_delivery_key(incident_id)]
    with session_factory() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.RECOVERED
        transition = session.scalar(
            select(IncidentStateTransitionRecord).where(
                IncidentStateTransitionRecord.incident_id == incident_id
            )
        )
        assert transition is not None
        assert transition.previous_status == initial_status
        assert transition.status == IncidentStatus.RECOVERED
        assert transition.initiator == IncidentTransitionInitiator.SYSTEM
        assert (
            session.scalar(
                select(func.count())
                .select_from(IncidentRecoveryNotificationRecord)
                .where(IncidentRecoveryNotificationRecord.incident_id == incident_id)
            )
            == 1
        )
        recovered_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_incident_events_total", "recovered"),
        )
        checker_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_recovery_checker_runs_total", "success"),
        )
        assert recovered_metric is not None and recovered_metric.value == 1
        assert checker_metric is not None and checker_metric.value == 2


@pytest.mark.parametrize("unfinished_state", ["RUNNING", "FAILED", None])
def test_incident_stays_active_until_every_task_succeeds(
    session_factory: SessionFactory,
    unfinished_state: str | None,
) -> None:
    incident_id, references = create_incident(session_factory)
    airflow = StubTaskStateClient(
        {
            references[0]: "SUCCESS",
            references[1]: unfinished_state,
        }
    )
    provider = StubRecoveryNotificationProvider()

    result = RecoveryChecker(
        session_factory,
        airflow_client=airflow,
        notification_provider=provider,
        clock=lambda: NOW,
    ).run_once()

    assert result.incidents_recovered == 0
    assert provider.payloads == []
    with session_factory() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.OPEN
        assert session.scalar(select(func.count()).select_from(IncidentStateTransitionRecord)) == 0


def test_airflow_failure_preserves_state_and_next_run_retries(
    session_factory: SessionFactory,
) -> None:
    incident_id, references = create_incident(session_factory, task_count=1)
    request = httpx.Request("GET", "http://airflow/api/v2/taskInstances")
    airflow = StubTaskStateClient(
        {references[0]: httpx.ConnectError("Airflow unavailable", request=request)}
    )
    provider = StubRecoveryNotificationProvider()
    checker = RecoveryChecker(
        session_factory,
        airflow_client=airflow,
        notification_provider=provider,
        clock=lambda: NOW,
    )

    failed = checker.run_once()
    with session_factory() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.OPEN
    airflow.states[references[0]] = "SUCCESS"
    retried = checker.run_once()

    assert failed.airflow_failures == 1
    assert failed.incidents_recovered == 0
    assert retried.incidents_recovered == 1


@pytest.mark.parametrize("terminal_status", [IncidentStatus.RESOLVED, IncidentStatus.IGNORED])
def test_resolved_and_ignored_incidents_are_not_checked(
    session_factory: SessionFactory,
    terminal_status: IncidentStatus,
) -> None:
    incident_id, references = create_incident(session_factory, status=terminal_status, task_count=1)
    airflow = StubTaskStateClient({references[0]: "SUCCESS"})

    result = RecoveryChecker(
        session_factory,
        airflow_client=airflow,
        notification_provider=StubRecoveryNotificationProvider(),
        clock=lambda: NOW,
    ).run_once()

    assert result.active_incidents_scanned == 0
    assert airflow.calls == []
    with session_factory() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        assert incident.status == terminal_status


def test_failed_recovery_notification_retries_without_duplicate_transition(
    session_factory: SessionFactory,
) -> None:
    incident_id, references = create_incident(session_factory, task_count=1)
    provider = StubRecoveryNotificationProvider(fail=True)
    checker = RecoveryChecker(
        session_factory,
        airflow_client=StubTaskStateClient({references[0]: "SUCCESS"}),
        notification_provider=provider,
        clock=lambda: NOW,
    )

    failed = checker.run_once()
    provider.fail = False
    retried = checker.run_once()

    assert failed.incidents_recovered == 1
    assert failed.notification_failures == 1
    assert retried.active_incidents_scanned == 0
    assert retried.notifications_delivered == 1
    assert provider.keys == [
        make_recovery_delivery_key(incident_id),
        make_recovery_delivery_key(incident_id),
    ]
    with session_factory() as session:
        delivery = session.scalar(
            select(IncidentRecoveryNotificationRecord).where(
                IncidentRecoveryNotificationRecord.incident_id == incident_id
            )
        )
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.DELIVERED
        assert delivery.attempt_count == 2
        assert delivery.last_error_category is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(IncidentStateTransitionRecord)
                .where(IncidentStateTransitionRecord.incident_id == incident_id)
            )
            == 1
        )


def test_airflow_client_filters_exact_mapped_task_and_retries_transient_error() -> None:
    reference = TaskInstanceReference(
        dag_id="orders/daily",
        dag_run_id="scheduled 2026-08-11",
        task_id="load.items",
        map_index=3,
    )
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == "Bearer token"
        assert request.url.params["task_id"] == "load.items"
        assert request.url.params["map_index"] == "3"
        assert request.url.params["limit"] == "2"
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "task_instances": [
                    {
                        "dag_id": reference.dag_id,
                        "dag_run_id": reference.dag_run_id,
                        "task_id": reference.task_id,
                        "map_index": reference.map_index,
                        "state": "success",
                    }
                ]
            },
        )

    sleeps: list[float] = []
    client = AirflowTaskStateClient(
        base_url="http://airflow:8080",
        api_token="token",
        max_attempts=2,
        retry_backoff_seconds=0.25,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        sleep=sleeps.append,
    )

    assert client.get_state(reference) == "SUCCESS"
    assert calls == 2
    assert sleeps == [0.25]


def test_airflow_client_rejects_ambiguous_response() -> None:
    reference = TaskInstanceReference("orders", "run", "load", -1)
    client = AirflowTaskStateClient(
        base_url="http://airflow:8080",
        api_token="token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    request=request,
                    json={"task_instances": []},
                )
            )
        ),
    )

    with pytest.raises(RecoveryCheckerError, match="exactly one"):
        client.get_state(reference)
