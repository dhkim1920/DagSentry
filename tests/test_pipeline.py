from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from dagsentry.db import SessionFactory
from dagsentry.domain.diagnosis import (
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.error_signature import FINGERPRINT_VERSION
from dagsentry.incident import transition_incident
from dagsentry.ingestion import ingest_failure_event
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    AIEvidence,
    LLMCallMetadata,
    LLMProviderError,
    LLMResult,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.models import (
    DiagnosisOutboxRecord,
    DiagnosisRecord,
    IncidentFailureRecord,
    IncidentRecord,
    NotificationDeliveryRecord,
    OperationalMetricCounterRecord,
    OutboxStatus,
)
from dagsentry.pipeline import DiagnosisPipeline
from dagsentry.rule_diagnosis import RULESET_VERSION, RuleEngine
from dagsentry.runtime_connections import (
    AirflowRuntimeConnection,
    AirflowRuntimeConnectionResolver,
    LLMRuntimeConnection,
    LLMRuntimeConnectionResolver,
    NotificationRuntimeConnection,
    NotificationRuntimeConnectionResolver,
    RuntimeConnectionError,
)
from dagsentry.task_logs import (
    LogCollectionStatus,
    LogUnavailableReason,
    TaskLogReference,
    TaskLogResult,
)
from dagsentry.worker import DiagnosisProcessingError, DiagnosisWorker

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


@dataclass
class StubLogFetcher:
    result: TaskLogResult
    calls: list[TaskLogReference] = field(default_factory=list)

    def fetch(self, reference: TaskLogReference) -> TaskLogResult:
        self.calls.append(reference)
        return self.result


@dataclass
class StubAirflowConnectionResolver:
    snapshot: AirflowRuntimeConnection
    calls: list[str] = field(default_factory=list)

    def resolve(self, environment: str) -> AirflowRuntimeConnection:
        self.calls.append(environment)
        return self.snapshot


class UnavailableAirflowConnectionResolver:
    def resolve(self, environment: str) -> AirflowRuntimeConnection:
        raise RuntimeConnectionError("database configuration unavailable")


@dataclass
class StubLLMConnectionResolver:
    snapshot: LLMRuntimeConnection
    calls: list[str] = field(default_factory=list)

    def resolve(self, environment: str) -> LLMRuntimeConnection:
        self.calls.append(environment)
        return self.snapshot


class UnavailableLLMConnectionResolver:
    def resolve(self, environment: str) -> LLMRuntimeConnection:
        raise RuntimeConnectionError("database configuration unavailable")


@dataclass
class StubNotificationConnectionResolver:
    snapshot: NotificationRuntimeConnection
    calls: list[str] = field(default_factory=list)

    def resolve(
        self, environment: str, *, connection_id: UUID | None = None
    ) -> NotificationRuntimeConnection:
        assert connection_id is None
        self.calls.append(environment)
        return self.snapshot


class UnavailableNotificationConnectionResolver:
    def resolve(
        self, environment: str, *, connection_id: UUID | None = None
    ) -> NotificationRuntimeConnection:
        assert connection_id is None
        raise RuntimeConnectionError("database configuration unavailable")


@dataclass
class StubLLMProvider:
    calls: list[AIDiagnosisRequest] = field(default_factory=list)
    fail: bool = False
    alter_evidence: bool = False

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        self.calls.append(request)
        if self.fail:
            raise LLMProviderError("provider unavailable")
        line = request.excerpt[-1]
        return LLMResult(
            diagnosis=AIDiagnosisResponse(
                classification=ErrorClassification.DAG_CODE,
                root_cause="The task rejected an invalid order.",
                confidence=0.9,
                evidence=[
                    AIEvidence(
                        line_id=line.line_id,
                        text="modified evidence" if self.alter_evidence else line.text,
                    )
                ],
                recommended_actions=["Validate input"],
                retry_decision=RetryDecision.NOT_RETRYABLE,
                operator_review_required=True,
            ),
            metadata=LLMCallMetadata(
                request_id="req_1",
                model="test-model",
                prompt_version="ai-v1",
                latency_ms=5,
                input_tokens=10,
                output_tokens=5,
            ),
        )


@dataclass
class StubNotificationProvider:
    name: str = "stub"
    payloads: list[NotificationPayload] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    fail: bool = False

    def send(
        self,
        payload: NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload,
        *,
        delivery_key: str,
    ) -> int:
        assert isinstance(payload, NotificationPayload)
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


def available_log(content: str = "ValueError: invalid order") -> TaskLogResult:
    return TaskLogResult(
        status=LogCollectionStatus.AVAILABLE,
        content=content,
        unavailable_reason=None,
        response_bytes=len(content.encode()),
        page_count=1,
    )


def unavailable_log() -> TaskLogResult:
    return TaskLogResult(
        status=LogCollectionStatus.LOG_UNAVAILABLE,
        content=None,
        unavailable_reason=LogUnavailableReason.AIRFLOW_UNAVAILABLE,
        response_bytes=0,
        page_count=0,
    )


def ingest(
    session_factory: SessionFactory,
    *,
    try_number: int = 1,
    map_index: int = -1,
) -> UUID:
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="test",
                dag_id="orders",
                dag_run_id="run",
                task_id="load",
                map_index=map_index,
                try_number=try_number,
                source=(
                    CollectionSource.RETRY_CALLBACK if try_number < 3 else CollectionSource.LISTENER
                ),
                state=(FailureState.UP_FOR_RETRY if try_number < 3 else FailureState.FAILED),
                observed_at=NOW,
                operator_type="PythonOperator",
            ),
        )
        return result.failure_event_id


def pipeline(
    session_factory: SessionFactory,
    *,
    log_fetcher: StubLogFetcher | None,
    notification: StubNotificationProvider | None,
    llm: StubLLMProvider | None,
    airflow_connection_resolver: AirflowRuntimeConnectionResolver | None = None,
    llm_connection_resolver: LLMRuntimeConnectionResolver | None = None,
    notification_connection_resolver: NotificationRuntimeConnectionResolver | None = None,
) -> DiagnosisPipeline:
    return DiagnosisPipeline(
        session_factory,
        log_fetcher=log_fetcher,
        log_processor=LogProcessor(),
        rule_engine=RuleEngine(),
        reuse_policy=DiagnosisReusePolicy(
            fingerprint_version=FINGERPRINT_VERSION,
            versions=DiagnosisVersions(1, "ai-v1", RULESET_VERSION),
            max_age=timedelta(days=30),
        ),
        notification_provider=notification,
        llm_provider=llm,
        airflow_ui_base_url="https://airflow.example",
        airflow_connection_resolver=airflow_connection_resolver,
        llm_connection_resolver=llm_connection_resolver,
        notification_connection_resolver=notification_connection_resolver,
    )


def test_vertical_pipeline_persists_valid_ai_and_sends_complete_notification(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    logs = StubLogFetcher(available_log())
    llm = StubLLMProvider()
    notifications = StubNotificationProvider()

    result = pipeline(
        session_factory,
        log_fetcher=logs,
        notification=notifications,
        llm=llm,
    ).process(failure_id)

    assert result.diagnosis_source == DiagnosisSource.AI
    assert len(logs.calls) == len(llm.calls) == len(notifications.payloads) == 1
    payload = notifications.payloads[0]
    assert payload.dag_id == "orders"
    assert payload.task_id == "load"
    assert payload.incident_id == result.incident_id
    assert payload.incident_status == IncidentStatus.OPEN
    with session_factory() as session:
        available_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_log_collection_total", "available"),
        )
        ai_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_diagnosis_outcomes_total", "ai_success"),
        )
        notification_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_notification_attempts_total", "delivered"),
        )
        incident_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_incident_events_total", "opened"),
        )
        assert available_metric is not None and available_metric.value == 1
        assert ai_metric is not None and ai_metric.value == 1
        assert notification_metric is not None and notification_metric.value == 1
        assert incident_metric is not None and incident_metric.value == 1
    assert payload.incident_failure_count == 1
    assert payload.failed_at.replace(tzinfo=UTC) == NOW
    assert payload.classification == ErrorClassification.DAG_CODE
    assert payload.root_cause == "The task rejected an invalid order."
    assert payload.evidence[0].text == "ValueError: invalid order"
    assert payload.error_signature is not None
    assert payload.recommended_actions == ["Validate input"]
    assert payload.retry_decision == RetryDecision.NOT_RETRYABLE
    assert payload.airflow_log_url is not None
    assert payload.is_rule_fallback is False
    with session_factory() as session:
        incident = session.get(IncidentRecord, result.incident_id)
        link = session.scalar(
            select(IncidentFailureRecord).where(
                IncidentFailureRecord.failure_event_id == failure_id
            )
        )
        assert incident is not None
        assert incident.status == IncidentStatus.OPEN
        assert link is not None
        assert link.incident_id == result.incident_id


def test_pipeline_resolves_one_airflow_snapshot_for_the_entire_job(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    logs = StubLogFetcher(available_log())
    resolver = StubAirflowConnectionResolver(
        AirflowRuntimeConnection(
            source="database",
            log_fetcher=logs,
            ui_base_url="https://database-airflow.example",
            version=4,
        )
    )
    notifications = StubNotificationProvider()

    pipeline(
        session_factory,
        log_fetcher=None,
        notification=notifications,
        llm=None,
        airflow_connection_resolver=resolver,
    ).process(failure_id)

    assert resolver.calls == ["test"]
    assert len(logs.calls) == 1
    assert notifications.payloads[0].airflow_log_url is not None
    assert notifications.payloads[0].airflow_log_url.startswith("https://database-airflow.example/")


def test_pipeline_records_runtime_connection_failure_as_retryable_stage(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)

    with pytest.raises(DiagnosisProcessingError) as raised:
        pipeline(
            session_factory,
            log_fetcher=None,
            notification=StubNotificationProvider(),
            llm=None,
            airflow_connection_resolver=UnavailableAirflowConnectionResolver(),
        ).process(failure_id)

    assert raised.value.retryable is True
    assert raised.value.stage == "airflow_connection"
    assert str(raised.value) == "Airflow runtime connection is unavailable"


def test_pipeline_uses_one_llm_snapshot_for_the_diagnosis_attempt(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    llm = StubLLMProvider()
    resolver = StubLLMConnectionResolver(
        LLMRuntimeConnection(
            source="database",
            provider=llm,
            version=5,
        )
    )

    result = pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log()),
        notification=StubNotificationProvider(),
        llm=None,
        llm_connection_resolver=resolver,
    ).process(failure_id)

    assert result.diagnosis_source == DiagnosisSource.AI
    assert resolver.calls == ["test"]
    assert len(llm.calls) == 1


def test_pipeline_records_llm_connection_failure_as_retryable_stage(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)

    with pytest.raises(DiagnosisProcessingError) as raised:
        pipeline(
            session_factory,
            log_fetcher=StubLogFetcher(available_log()),
            notification=StubNotificationProvider(),
            llm=None,
            llm_connection_resolver=UnavailableLLMConnectionResolver(),
        ).process(failure_id)

    assert raised.value.retryable is True
    assert raised.value.stage == "llm_connection"
    assert str(raised.value) == "LLM runtime connection is unavailable"


def test_pipeline_uses_notification_snapshot_for_delivery(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    notifications = StubNotificationProvider(name="slack")
    resolver = StubNotificationConnectionResolver(
        NotificationRuntimeConnection(
            source="database",
            provider=notifications,
            version=6,
        )
    )

    pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log()),
        notification=None,
        llm=None,
        notification_connection_resolver=resolver,
    ).process(failure_id)

    assert resolver.calls == ["test"]
    assert len(notifications.payloads) == 1
    with session_factory() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.provider == "slack"


def test_pipeline_records_notification_connection_failure_as_retryable_stage(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)

    with pytest.raises(DiagnosisProcessingError) as raised:
        pipeline(
            session_factory,
            log_fetcher=StubLogFetcher(available_log()),
            notification=None,
            llm=None,
            notification_connection_resolver=UnavailableNotificationConnectionResolver(),
        ).process(failure_id)

    assert raised.value.retryable is True
    assert raised.value.stage == "notification_connection"
    assert str(raised.value) == "Notification runtime connection is unavailable"


@pytest.mark.parametrize("failure_mode", ["unconfigured", "provider_error", "invalid_evidence"])
def test_ai_failure_modes_end_with_explicit_rule_fallback(
    session_factory: SessionFactory,
    failure_mode: str,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    llm = None if failure_mode == "unconfigured" else StubLLMProvider()
    if llm is not None:
        llm.fail = failure_mode == "provider_error"
        llm.alter_evidence = failure_mode == "invalid_evidence"
    notifications = StubNotificationProvider()

    result = pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log()),
        notification=notifications,
        llm=llm,
    ).process(failure_id)

    assert result.diagnosis_source == DiagnosisSource.RULE
    assert notifications.payloads[0].is_rule_fallback is True
    with session_factory() as session:
        expected_metric = {
            "unconfigured": "provider_not_configured",
            "provider_error": "provider_error",
            "invalid_evidence": "evidence_rejected",
        }[failure_mode]
        outcome_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_diagnosis_outcomes_total", expected_metric),
        )
        assert outcome_metric is not None and outcome_metric.value == 1
        records = session.scalars(
            select(DiagnosisRecord).where(DiagnosisRecord.failure_event_id == failure_id)
        ).all()
        if failure_mode == "invalid_evidence":
            assert len(records) == 2
            assert any(
                record.validation_status == DiagnosisValidationStatus.REJECTED for record in records
            )
        else:
            assert len(records) == 1


def test_log_unavailable_skips_ai_and_still_notifies_rule_result(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    llm = StubLLMProvider()
    notifications = StubNotificationProvider()

    result = pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(unavailable_log()),
        notification=notifications,
        llm=llm,
    ).process(failure_id)

    assert result.diagnosis_source == DiagnosisSource.RULE
    assert llm.calls == []
    assert notifications.payloads[0].classification == ErrorClassification.UNKNOWN
    with session_factory() as session:
        log_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_log_collection_total", "unavailable"),
        )
        diagnosis_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_diagnosis_outcomes_total", "log_unavailable"),
        )
        assert log_metric is not None and log_metric.value == 1
        assert diagnosis_metric is not None and diagnosis_metric.value == 1


def test_notification_retry_resumes_persisted_diagnosis_without_repeating_ai(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    logs = StubLogFetcher(available_log())
    llm = StubLLMProvider()
    notifications = StubNotificationProvider(fail=True)
    diagnosis_pipeline = pipeline(
        session_factory,
        log_fetcher=logs,
        notification=notifications,
        llm=llm,
    )

    with pytest.raises(DiagnosisProcessingError):
        diagnosis_pipeline.process(failure_id)
    notifications.fail = False
    result = diagnosis_pipeline.process(failure_id)

    assert result.reused_existing_diagnosis is True
    assert len(logs.calls) == 1
    assert len(llm.calls) == 1
    assert len(notifications.payloads) == 2
    assert notifications.keys[0] == notifications.keys[1]
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DiagnosisRecord)
                .where(DiagnosisRecord.failure_event_id == failure_id)
            )
            == 1
        )
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.attempt_count == 2


def test_second_equivalent_failure_reuses_first_ai_diagnosis(
    session_factory: SessionFactory,
) -> None:
    first_id = ingest(session_factory, try_number=1)
    second_id = ingest(session_factory, try_number=2)
    logs = StubLogFetcher(available_log())
    llm = StubLLMProvider()
    notifications = StubNotificationProvider()
    diagnosis_pipeline = pipeline(
        session_factory,
        log_fetcher=logs,
        notification=notifications,
        llm=llm,
    )

    first = diagnosis_pipeline.process(first_id)
    second = diagnosis_pipeline.process(second_id)

    assert first.diagnosis_source == DiagnosisSource.AI
    assert second.diagnosis_source == DiagnosisSource.REUSED
    assert second.incident_id == first.incident_id
    assert len(llm.calls) == 1
    assert len(notifications.payloads) == 1
    assert second.notification.suppressed is True
    with session_factory() as session:
        reuse_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_diagnosis_outcomes_total", "reused"),
        )
        assert reuse_metric is not None and reuse_metric.value == 1
        deliveries = session.scalars(
            select(NotificationDeliveryRecord).order_by(NotificationDeliveryRecord.created_at)
        ).all()
        assert {delivery.status for delivery in deliveries} == {
            NotificationDeliveryStatus.DELIVERED,
            NotificationDeliveryStatus.SUPPRESSED,
        }
        suppressed = next(
            delivery
            for delivery in deliveries
            if delivery.status == NotificationDeliveryStatus.SUPPRESSED
        )
        assert suppressed.suppression_reason == "REPEATED_ACTIVE_INCIDENT"
        assert suppressed.attempt_count == 0
        assert suppressed.payload["incident_failure_count"] == 2
        assert suppressed.payload["diagnosis_source"] == DiagnosisSource.REUSED


@pytest.mark.parametrize("tries", [(1, 2, 3, 4), (3, 4)])
def test_final_failure_is_delivered_once_per_active_incident(
    session_factory: SessionFactory,
    tries: tuple[int, ...],
) -> None:
    logs = StubLogFetcher(available_log())
    notifications = StubNotificationProvider()
    service = pipeline(
        session_factory, log_fetcher=logs, notification=notifications, llm=StubLLMProvider()
    )
    ids = [ingest(session_factory, try_number=number) for number in tries]
    results = [service.process(failure_id) for failure_id in ids]
    expected = [ids[0], ids[2]] if tries[0] == 1 else [ids[0]]
    assert [payload.failure_event_id for payload in notifications.payloads] == expected
    assert notifications.payloads[-1].failure_state == FailureState.FAILED
    assert results[-1].notification.suppressed
    with session_factory() as session:
        incident = session.get(IncidentRecord, results[0].incident_id)
        assert incident is not None
        assert incident.final_failure_event_id == expected[-1]
        record = session.get(NotificationDeliveryRecord, results[-1].notification.delivery_id)
        assert record is not None
        assert record.suppression_reason == "REPEATED_FINAL_FAILURE"


def test_final_failure_retry_keeps_claim_and_delivery_key(session_factory: SessionFactory) -> None:
    logs = StubLogFetcher(available_log())
    notifications = StubNotificationProvider()
    llm = StubLLMProvider()
    service = pipeline(session_factory, log_fetcher=logs, notification=notifications, llm=llm)
    first = ingest(session_factory, try_number=1)
    final = ingest(session_factory, try_number=3)
    service.process(first)
    notifications.fail = True
    with pytest.raises(DiagnosisProcessingError):
        service.process(final)
    log_calls = len(logs.calls)
    ai_calls = len(llm.calls)
    notifications.fail = False
    result = service.process(final)
    assert result.notification.delivered
    assert notifications.keys[-1] == notifications.keys[-2]
    assert len(logs.calls) == log_calls
    assert len(llm.calls) == ai_calls
    service.process(final)
    assert len(notifications.keys) == 3


def test_failure_after_resolved_incident_sends_a_new_full_notification(
    session_factory: SessionFactory,
) -> None:
    first_id = ingest(session_factory, try_number=1)
    second_id = ingest(session_factory, try_number=2)
    notifications = StubNotificationProvider()
    diagnosis_pipeline = pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log()),
        notification=notifications,
        llm=StubLLMProvider(),
    )
    first = diagnosis_pipeline.process(first_id)
    with session_factory() as session:
        transition_incident(
            session,
            incident_id=first.incident_id,
            target=IncidentStatus.RESOLVED,
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor="oncall@example.com",
            expected_status=IncidentStatus.OPEN,
        )

    second = diagnosis_pipeline.process(second_id)

    assert second.incident_id != first.incident_id
    assert second.notification.suppressed is False
    assert len(notifications.payloads) == 2


def test_secret_does_not_reach_ai_database_or_notification(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    secret = "never-expose-this"
    llm = StubLLMProvider()
    notifications = StubNotificationProvider()

    pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log(f"ValueError: password={secret}")),
        notification=notifications,
        llm=llm,
    ).process(failure_id)

    assert secret not in repr(llm.calls)
    assert secret not in repr(notifications.payloads)
    with session_factory() as session:
        diagnoses = session.scalars(select(DiagnosisRecord)).all()
        deliveries = session.scalars(select(NotificationDeliveryRecord)).all()
        stored_diagnosis_content = [
            {
                "root_cause": diagnosis.root_cause,
                "evidence": diagnosis.evidence,
                "recommended_actions": diagnosis.recommended_actions,
                "validation_errors": diagnosis.validation_errors,
            }
            for diagnosis in diagnoses
        ]
        assert secret not in repr(stored_diagnosis_content)
        assert secret not in repr([delivery.payload for delivery in deliveries])


def test_outbox_worker_completes_with_rule_fallback_when_llm_is_unavailable(
    session_factory: SessionFactory,
) -> None:
    failure_id = ingest(session_factory, try_number=3)
    notifications = StubNotificationProvider()
    diagnosis_pipeline = pipeline(
        session_factory,
        log_fetcher=StubLogFetcher(available_log()),
        notification=notifications,
        llm=StubLLMProvider(fail=True),
    )
    worker = DiagnosisWorker(
        session_factory,
        diagnosis_pipeline.handle,
        "pipeline-worker",
        clock=lambda: datetime.now(UTC),
    )

    assert worker.process_one() is True

    with session_factory() as session:
        outbox = session.scalar(
            select(DiagnosisOutboxRecord).where(
                DiagnosisOutboxRecord.failure_event_id == failure_id
            )
        )
        assert outbox is not None
        assert outbox.status == OutboxStatus.COMPLETED
        effective = session.scalar(
            select(DiagnosisRecord).where(
                DiagnosisRecord.failure_event_id == failure_id,
                DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
            )
        )
        assert effective is not None
        assert effective.source == DiagnosisSource.RULE
