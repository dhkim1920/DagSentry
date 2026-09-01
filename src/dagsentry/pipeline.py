"""Complete v0.1 Failure Diagnosis pipeline used by the Outbox Worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlencode
from uuid import UUID

from sqlalchemy import func, select

from dagsentry.ai_diagnosis import (
    AIDiagnosisOutcomeSource,
    RuleFallbackReason,
    diagnose_with_fallback,
)
from dagsentry.db import SessionFactory
from dagsentry.diagnosis import persist_ai_validation, persist_rule_diagnosis, reuse_diagnosis
from dagsentry.domain.diagnosis import (
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
)
from dagsentry.domain.notification import (
    NotificationEvidence,
    NotificationPayload,
    NotificationProvider,
    NotificationProviderError,
    NotificationSuppressionReason,
)
from dagsentry.error_signature import (
    ErrorSignatureCandidate,
    SignatureStatus,
    build_error_signature,
    persist_error_signature,
    signature_input_from_excerpt,
)
from dagsentry.incident import correlate_failure
from dagsentry.llm import (
    AIDiagnosisRequest,
    ErrorSignatureContext,
    ExcerptLineContext,
    FailureMetadata,
    LLMProvider,
    RuleDiagnosisContext,
)
from dagsentry.log_processing import LogProcessor, RelevantLogExcerpt
from dagsentry.metrics import record_counter
from dagsentry.models import (
    DiagnosisRecord,
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
)
from dagsentry.notification import (
    NotificationDeliveryResult,
    deliver_notification,
    suppress_notification,
)
from dagsentry.rule_diagnosis import RuleDiagnosisInput, RuleDiagnosisResult, RuleEngine
from dagsentry.runtime_connections import (
    AirflowRuntimeConnectionResolver,
    LLMRuntimeConnectionResolver,
    NotificationRuntimeConnectionResolver,
    RuntimeConnectionError,
)
from dagsentry.task_logs import LogCollectionStatus, TaskLogFetcher, TaskLogReference
from dagsentry.worker import DiagnosisProcessingError, PermanentDiagnosisError


@dataclass(frozen=True)
class DiagnosisPipelineResult:
    """Observable result of processing one Failure Event."""

    diagnosis_id: UUID
    diagnosis_source: DiagnosisSource
    incident_id: UUID
    notification: NotificationDeliveryResult
    reused_existing_diagnosis: bool


class FailureEventNotFoundError(PermanentDiagnosisError):
    """The Outbox references a Failure Event that cannot be processed."""

    def __init__(self, failure_event_id: UUID) -> None:
        super().__init__(f"Failure Event {failure_event_id} was not found", stage="load_event")


class DiagnosisPipeline:
    """Diagnose, validate, persist, and notify one Failure Event idempotently."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        log_fetcher: TaskLogFetcher | None = None,
        log_processor: LogProcessor,
        rule_engine: RuleEngine,
        reuse_policy: DiagnosisReusePolicy,
        notification_provider: NotificationProvider | None = None,
        llm_provider: LLMProvider | None = None,
        airflow_ui_base_url: str | None = None,
        airflow_connection_resolver: AirflowRuntimeConnectionResolver | None = None,
        llm_connection_resolver: LLMRuntimeConnectionResolver | None = None,
        notification_connection_resolver: NotificationRuntimeConnectionResolver | None = None,
    ) -> None:
        if (log_fetcher is None) == (airflow_connection_resolver is None):
            raise ValueError("exactly one Airflow connection source must be configured")
        if llm_provider is not None and llm_connection_resolver is not None:
            raise ValueError("only one LLM connection source may be configured")
        if (notification_provider is None) == (notification_connection_resolver is None):
            raise ValueError("exactly one Notification connection source must be configured")
        self.session_factory = session_factory
        self.log_fetcher = log_fetcher
        self.log_processor = log_processor
        self.rule_engine = rule_engine
        self.reuse_policy = reuse_policy
        self.notification_provider = notification_provider
        self.llm_provider = llm_provider
        self.airflow_ui_base_url = airflow_ui_base_url
        self.airflow_connection_resolver = airflow_connection_resolver
        self.llm_connection_resolver = llm_connection_resolver
        self.notification_connection_resolver = notification_connection_resolver

    def handle(self, failure_event_id: UUID) -> None:
        """Worker-compatible handler."""
        self.process(failure_event_id)

    def process(self, failure_event_id: UUID) -> DiagnosisPipelineResult:
        """Run the vertical pipeline, resuming from a persisted Diagnosis on retry."""
        event = self._load_event(failure_event_id)
        log_fetcher = self.log_fetcher
        airflow_ui_base_url = self.airflow_ui_base_url
        if self.airflow_connection_resolver is not None:
            try:
                connection = self.airflow_connection_resolver.resolve(event.environment)
            except RuntimeConnectionError as error:
                raise DiagnosisProcessingError(
                    "Airflow runtime connection is unavailable",
                    stage="airflow_connection",
                ) from error
            log_fetcher = connection.log_fetcher
            airflow_ui_base_url = connection.ui_base_url
        assert log_fetcher is not None
        notification_provider = self.notification_provider
        if self.notification_connection_resolver is not None:
            try:
                notification_connection = self.notification_connection_resolver.resolve(
                    event.environment
                )
            except RuntimeConnectionError as error:
                raise DiagnosisProcessingError(
                    "Notification runtime connection is unavailable",
                    stage="notification_connection",
                ) from error
            notification_provider = notification_connection.provider
        assert notification_provider is not None
        effective = self._find_effective_diagnosis(failure_event_id)
        reused_existing = effective is not None
        if effective is None:
            llm_provider = self.llm_provider
            if self.llm_connection_resolver is not None:
                try:
                    llm_connection = self.llm_connection_resolver.resolve(event.environment)
                except RuntimeConnectionError as error:
                    raise DiagnosisProcessingError(
                        "LLM runtime connection is unavailable",
                        stage="llm_connection",
                    ) from error
                llm_provider = llm_connection.provider
            effective = self._diagnose(event, log_fetcher, llm_provider)

        with self.session_factory() as session:
            incident = correlate_failure(
                session,
                failure_event_id=event.id,
                error_signature_id=effective.error_signature_id,
            )

        payload = self._notification_payload(
            event,
            effective,
            incident.incident_id,
            airflow_ui_base_url,
        )
        try:
            if incident.is_initial_failure:
                notification = deliver_notification(
                    self.session_factory,
                    provider=notification_provider,
                    payload=payload,
                )
            else:
                notification = suppress_notification(
                    self.session_factory,
                    provider_name=notification_provider.name,
                    payload=payload,
                    reason=NotificationSuppressionReason.REPEATED_ACTIVE_INCIDENT,
                )
        except NotificationProviderError as error:
            error_type = DiagnosisProcessingError if error.retryable else PermanentDiagnosisError
            raise error_type(str(error), stage="notification") from error
        return DiagnosisPipelineResult(
            diagnosis_id=effective.id,
            diagnosis_source=effective.source,
            incident_id=incident.incident_id,
            notification=notification,
            reused_existing_diagnosis=reused_existing,
        )

    def _load_event(self, failure_event_id: UUID) -> FailureEventRecord:
        with self.session_factory() as session:
            event = session.get(FailureEventRecord, failure_event_id)
            if event is None:
                raise FailureEventNotFoundError(failure_event_id)
            session.expunge(event)
            return event

    def _find_effective_diagnosis(self, failure_event_id: UUID) -> DiagnosisRecord | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(DiagnosisRecord)
                .where(
                    DiagnosisRecord.failure_event_id == failure_event_id,
                    DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
                )
                .order_by(DiagnosisRecord.created_at.desc(), DiagnosisRecord.id.desc())
                .limit(1)
            )
            if record is not None:
                session.expunge(record)
            return record

    def _diagnose(
        self,
        event: FailureEventRecord,
        log_fetcher: TaskLogFetcher,
        llm_provider: LLMProvider | None,
    ) -> DiagnosisRecord:
        log_result = log_fetcher.fetch(
            TaskLogReference(
                dag_id=event.dag_id,
                dag_run_id=event.dag_run_id,
                task_id=event.task_id,
                map_index=event.map_index,
                try_number=event.try_number,
            )
        )
        record_counter(
            self.session_factory,
            "dagsentry_log_collection_total",
            ("available" if log_result.status == LogCollectionStatus.AVAILABLE else "unavailable"),
        )
        excerpt = self.log_processor.process_result(log_result)
        rule = self.rule_engine.diagnose(
            RuleDiagnosisInput(operator_type=event.operator_type, excerpt=excerpt)
        )
        candidate = build_error_signature(
            signature_input_from_excerpt(event.operator_type, excerpt)
        )
        with self.session_factory() as session:
            signature = persist_error_signature(session, candidate)

        with self.session_factory() as session:
            reused = reuse_diagnosis(
                session,
                failure_event_id=event.id,
                error_signature_id=signature.signature_id,
                policy=self.reuse_policy,
            )
        if reused is not None:
            record_counter(
                self.session_factory,
                "dagsentry_diagnosis_outcomes_total",
                "reused",
            )
            return self._required_diagnosis(reused.diagnosis_id)

        request = _ai_request(event, rule, candidate, excerpt)
        outcome = diagnose_with_fallback(
            provider=llm_provider,
            request=request,
            rule_diagnosis=rule,
            log_processor=self.log_processor,
        )
        outcome_label = {
            None: "ai_success",
            RuleFallbackReason.EVIDENCE_REJECTED: "evidence_rejected",
            RuleFallbackReason.PROVIDER_ERROR: "provider_error",
            RuleFallbackReason.PROVIDER_NOT_CONFIGURED: "provider_not_configured",
            RuleFallbackReason.LOG_UNAVAILABLE: "log_unavailable",
        }[outcome.fallback_reason]
        record_counter(
            self.session_factory,
            "dagsentry_diagnosis_outcomes_total",
            outcome_label,
        )
        with self.session_factory() as session:
            if outcome.source == AIDiagnosisOutcomeSource.AI:
                assert outcome.ai_result is not None and outcome.validation is not None
                persisted = persist_ai_validation(
                    session,
                    failure_event_id=event.id,
                    error_signature_id=signature.signature_id,
                    ai_result=outcome.ai_result,
                    validation=outcome.validation,
                    rule_diagnosis=rule,
                    excerpt=_required_excerpt(excerpt),
                )
                diagnosis_id = persisted.ai_diagnosis_id
            elif outcome.fallback_reason == RuleFallbackReason.EVIDENCE_REJECTED:
                assert outcome.ai_result is not None and outcome.validation is not None
                persisted = persist_ai_validation(
                    session,
                    failure_event_id=event.id,
                    error_signature_id=signature.signature_id,
                    ai_result=outcome.ai_result,
                    validation=outcome.validation,
                    rule_diagnosis=rule,
                    excerpt=_required_excerpt(excerpt),
                )
                assert persisted.rule_fallback_diagnosis_id is not None
                diagnosis_id = persisted.rule_fallback_diagnosis_id
            else:
                diagnosis_id = persist_rule_diagnosis(
                    session,
                    failure_event_id=event.id,
                    error_signature_id=signature.signature_id,
                    result=rule,
                    excerpt=excerpt,
                )
        return self._required_diagnosis(diagnosis_id)

    def _required_diagnosis(self, diagnosis_id: UUID) -> DiagnosisRecord:
        with self.session_factory() as session:
            record = session.get(DiagnosisRecord, diagnosis_id)
            if record is None:  # pragma: no cover
                raise RuntimeError("Persisted Diagnosis was not found")
            session.expunge(record)
            return record

    def _notification_payload(
        self,
        event: FailureEventRecord,
        effective: DiagnosisRecord,
        incident_id: UUID,
        airflow_ui_base_url: str | None,
    ) -> NotificationPayload:
        content = effective
        with self.session_factory() as session:
            if effective.source == DiagnosisSource.REUSED:
                assert effective.reused_from_diagnosis_id is not None
                original = session.get(DiagnosisRecord, effective.reused_from_diagnosis_id)
                if original is None:  # pragma: no cover
                    raise RuntimeError("Reused Diagnosis original was not found")
                content = original
            signature = (
                session.get(ErrorSignatureRecord, effective.error_signature_id)
                if effective.error_signature_id is not None
                else None
            )
            classification = content.classification
            confidence = content.confidence
            retry_decision = content.retry_decision
            assert classification is not None and confidence is not None
            assert retry_decision is not None
            evidence = [_notification_evidence(item) for item in content.evidence or []]
            incident = session.get(IncidentRecord, incident_id)
            if incident is None:  # pragma: no cover - database invariant
                raise RuntimeError("Incident was not found")
            incident_failure_count = session.scalar(
                select(func.count())
                .select_from(IncidentFailureRecord)
                .where(IncidentFailureRecord.incident_id == incident_id)
            )
            return NotificationPayload(
                failure_event_id=event.id,
                diagnosis_id=effective.id,
                incident_id=incident.id,
                incident_status=incident.status,
                incident_failure_count=incident_failure_count or 1,
                environment=event.environment,
                dag_id=event.dag_id,
                dag_run_id=event.dag_run_id,
                task_id=event.task_id,
                map_index=event.map_index,
                try_number=event.try_number,
                failed_at=_as_utc(event.observed_at),
                classification=classification,
                root_cause=content.root_cause,
                confidence=confidence,
                evidence=evidence,
                error_signature=signature.fingerprint if signature is not None else None,
                recommended_actions=list(content.recommended_actions or []),
                retry_decision=retry_decision,
                airflow_log_url=self._airflow_log_url(event, airflow_ui_base_url),
                diagnosis_source=effective.source,
                is_rule_fallback=content.source == DiagnosisSource.RULE,
            )

    def _airflow_log_url(
        self,
        event: FailureEventRecord,
        airflow_ui_base_url: str | None,
    ) -> str | None:
        if airflow_ui_base_url is None:
            return None
        path = (
            f"/dags/{quote(event.dag_id, safe='')}/runs/"
            f"{quote(event.dag_run_id, safe='')}/tasks/{quote(event.task_id, safe='')}"
        )
        query = urlencode({"map_index": event.map_index, "try_number": event.try_number})
        return f"{airflow_ui_base_url.rstrip('/')}{path}?{query}"


def _ai_request(
    event: FailureEventRecord,
    rule: RuleDiagnosisResult,
    signature: ErrorSignatureCandidate,
    excerpt: RelevantLogExcerpt | None,
) -> AIDiagnosisRequest:
    canonical = signature.canonical_error
    signature_context = None
    if signature.status == SignatureStatus.SIGNABLE:
        assert canonical is not None and signature.fingerprint is not None
        signature_context = ErrorSignatureContext(
            fingerprint=signature.fingerprint,
            fingerprint_version=signature.fingerprint_version,
            operator_type=canonical.operator_type,
            exception_class=canonical.exception_class,
            vendor_error_code=canonical.vendor_error_code,
            normalized_message=canonical.normalized_message,
            application_stack_frame=canonical.application_stack_frame,
        )
    return AIDiagnosisRequest(
        metadata=FailureMetadata(
            environment=event.environment,
            dag_id=event.dag_id,
            dag_run_id=event.dag_run_id,
            task_id=event.task_id,
            map_index=event.map_index,
            try_number=event.try_number,
            state=event.state.value,
            observed_at=event.observed_at,
            operator_type=event.operator_type,
        ),
        rule_diagnosis=RuleDiagnosisContext(
            classification=rule.classification,
            matched_rule=rule.matched_rule,
            ruleset_version=rule.ruleset_version,
            confidence=rule.confidence,
            confidence_reason=rule.confidence_reason,
            extracted_values=tuple(
                {"name": value.name, "value": value.value} for value in rule.extracted_values
            ),
            evidence_line_ids=rule.evidence_line_ids,
        ),
        error_signature=signature_context,
        excerpt=tuple(
            ExcerptLineContext(line_id=line.line_id, text=line.text) for line in excerpt.lines
        )
        if excerpt is not None
        else (),
    )


def _required_excerpt(excerpt: RelevantLogExcerpt | None) -> RelevantLogExcerpt:
    if excerpt is None:  # pragma: no cover - AI cannot run without an excerpt
        raise RuntimeError("AI result requires a Relevant Log Excerpt")
    return excerpt


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _notification_evidence(item: dict[str, object]) -> NotificationEvidence:
    line_id = item.get("line_id")
    text = item.get("text")
    if not isinstance(line_id, int) or isinstance(line_id, bool) or not isinstance(text, str):
        raise RuntimeError("Stored Diagnosis evidence is invalid")
    return NotificationEvidence(line_id=line_id, text=text)
