"""Persistent DagSentry records."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from dagsentry.db import Base
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose, ConnectionTestStatus
from dagsentry.domain.diagnosis import (
    DiagnosisSource,
    DiagnosisValidationStatus,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureState
from dagsentry.domain.human_diagnosis import HumanDiagnosisAction
from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.notification import NotificationDeliveryStatus


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for ORM defaults."""
    return datetime.now(UTC)


class OutboxStatus(StrEnum):
    """Lifecycle of a diagnosis outbox job."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    DEAD = "DEAD"


class DailyReportScheduleRunTrigger(StrEnum):
    """Origin of one Daily Report scheduler execution."""

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class DailyReportScheduleRunStatus(StrEnum):
    """Durable lifecycle of one requested Daily Report execution."""

    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class UserRecord(Base):
    """One local DagSentry human account."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(250))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, create_constraint=True, name="user_role")
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, create_constraint=True, name="user_status"),
        default=UserStatus.ACTIVE,
    )
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserSessionRecord(Base):
    """Hash-only storage for one opaque browser session token."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_user_sessions_token_hash_length"),
        CheckConstraint("length(csrf_token_hash) = 64", name="ck_user_sessions_csrf_hash_length"),
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        Index("ix_user_sessions_user_expiry", "user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdminAuditEventRecord(Base):
    """Append-only record of an administrative identity or configuration change."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (Index("ix_admin_audit_events_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID] = mapped_column()
    change_summary: Mapped[dict[str, object]] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ManagedConnectionRecord(Base):
    """One versioned outbound connection for an environment and purpose."""

    __tablename__ = "managed_connections"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_managed_connections_version"),
        CheckConstraint(
            "(secret_ciphertext IS NULL AND secret_nonce IS NULL AND secret_key_version IS NULL) "
            "OR (secret_ciphertext IS NOT NULL AND secret_nonce IS NOT NULL "
            "AND secret_key_version IS NOT NULL)",
            name="ck_managed_connections_secret_shape",
        ),
        CheckConstraint(
            "secret_ciphertext IS NULL OR length(secret_ciphertext) >= 16",
            name="ck_managed_connections_ciphertext_length",
        ),
        CheckConstraint(
            "secret_nonce IS NULL OR length(secret_nonce) = 12",
            name="ck_managed_connections_nonce_length",
        ),
        CheckConstraint(
            "secret_key_version IS NULL OR secret_key_version >= 1",
            name="ck_managed_connections_key_version",
        ),
        CheckConstraint(
            "(purpose = 'AIRFLOW' AND provider = 'AIRFLOW') OR "
            "(purpose = 'LLM' AND provider IN "
            "('OLLAMA', 'OPENAI', 'AZURE_OPENAI', 'ANTHROPIC', 'BEDROCK')) OR "
            "(purpose = 'NOTIFICATION' AND provider IN "
            "('WEBHOOK', 'SLACK', 'TEAMS', 'DISCORD'))",
            name="ck_managed_connections_provider_purpose",
        ),
        UniqueConstraint(
            "environment",
            "purpose",
            name="uq_managed_connections_environment_purpose",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[ConnectionPurpose] = mapped_column(
        Enum(
            ConnectionPurpose,
            native_enum=False,
            create_constraint=True,
            name="managed_connection_purpose",
        )
    )
    provider: Mapped[ConnectionProvider] = mapped_column(
        Enum(
            ConnectionProvider,
            native_enum=False,
            create_constraint=True,
            name="managed_connection_provider",
        )
    )
    display_name: Mapped[str] = mapped_column(String(250))
    non_secret_config: Mapped[dict[str, object]] = mapped_column(JSON)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_key_version: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[ConnectionTestStatus | None] = mapped_column(
        Enum(
            ConnectionTestStatus,
            native_enum=False,
            create_constraint=True,
            name="managed_connection_test_status",
        )
    )
    last_test_error_category: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FailureEventRecord(Base):
    """One failed Airflow Task Try."""

    __tablename__ = "failure_events"
    __table_args__ = (
        CheckConstraint("map_index >= -1", name="ck_failure_events_map_index"),
        CheckConstraint("try_number >= 1", name="ck_failure_events_try_number"),
        CheckConstraint("event_key_version >= 1", name="ck_failure_events_event_key_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    event_key_version: Mapped[int] = mapped_column(SmallInteger)
    environment: Mapped[str] = mapped_column(String(64))
    dag_id: Mapped[str] = mapped_column(String(250))
    dag_run_id: Mapped[str] = mapped_column(String(250))
    task_id: Mapped[str] = mapped_column(String(250))
    map_index: Mapped[int] = mapped_column(Integer)
    try_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[CollectionSource] = mapped_column(
        Enum(CollectionSource, native_enum=False, create_constraint=True, name="collection_source")
    )
    state: Mapped[FailureState] = mapped_column(
        Enum(FailureState, native_enum=False, create_constraint=True, name="failure_state")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operator_type: Mapped[str | None] = mapped_column(String(250))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiagnosisOutboxRecord(Base):
    """A reliable request to diagnose one Failure Event."""

    __tablename__ = "diagnosis_outbox"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_diagnosis_outbox_attempt_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    failure_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("failure_events.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False, create_constraint=True, name="outbox_status"),
        default=OutboxStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(250))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ErrorSignatureRecord(Base):
    """One versioned canonical error shared by equivalent failures."""

    __tablename__ = "error_signatures"
    __table_args__ = (
        CheckConstraint("fingerprint_version >= 1", name="ck_error_signatures_version"),
        UniqueConstraint(
            "fingerprint_version",
            "fingerprint",
            name="uq_error_signatures_version_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    fingerprint_version: Mapped[int] = mapped_column(SmallInteger)
    fingerprint: Mapped[str] = mapped_column(String(64))
    operator_type: Mapped[str | None] = mapped_column(String(250))
    exception_class: Mapped[str | None] = mapped_column(String(500))
    vendor_error_code: Mapped[str | None] = mapped_column(String(250))
    normalized_message: Mapped[str | None] = mapped_column(Text)
    application_stack_frame: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiagnosisRecord(Base):
    """A materialized Rule/AI Diagnosis or a reference to a reusable original."""

    __tablename__ = "diagnoses"
    __table_args__ = (
        CheckConstraint("diagnosis_schema_version >= 1", name="ck_diagnoses_schema_version"),
        CheckConstraint(
            "rule_version IS NULL OR rule_version >= 1", name="ck_diagnoses_rule_version"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_diagnoses_confidence",
        ),
        CheckConstraint(
            "(source = 'REUSED' AND reused_from_diagnosis_id IS NOT NULL "
            "AND classification IS NULL AND root_cause IS NULL AND confidence IS NULL "
            "AND confidence_reason IS NULL AND matched_rule IS NULL "
            "AND extracted_values IS NULL AND evidence IS NULL "
            "AND recommended_actions IS NULL AND retry_decision IS NULL "
            "AND operator_review_required IS NULL AND validation_errors IS NULL) OR "
            "(source <> 'REUSED' AND reused_from_diagnosis_id IS NULL "
            "AND classification IS NOT NULL AND confidence IS NOT NULL "
            "AND extracted_values IS NOT NULL AND evidence IS NOT NULL "
            "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL)",
            name="ck_diagnoses_reused_shape",
        ),
        Index(
            "ix_diagnoses_reuse_lookup",
            "error_signature_id",
            "validation_status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    failure_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("failure_events.id", ondelete="CASCADE")
    )
    error_signature_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("error_signatures.id"))
    source: Mapped[DiagnosisSource] = mapped_column(
        Enum(DiagnosisSource, native_enum=False, create_constraint=True, name="diagnosis_source")
    )
    validation_status: Mapped[DiagnosisValidationStatus] = mapped_column(
        Enum(
            DiagnosisValidationStatus,
            native_enum=False,
            create_constraint=True,
            name="diagnosis_validation_status",
        )
    )
    classification: Mapped[ErrorClassification | None] = mapped_column(
        Enum(
            ErrorClassification,
            native_enum=False,
            create_constraint=True,
            name="error_classification",
        )
    )
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_reason: Mapped[str | None] = mapped_column(Text)
    matched_rule: Mapped[str | None] = mapped_column(String(250))
    extracted_values: Mapped[list[dict[str, str]] | None] = mapped_column(JSON(none_as_null=True))
    evidence: Mapped[list[dict[str, object]] | None] = mapped_column(JSON(none_as_null=True))
    recommended_actions: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    retry_decision: Mapped[RetryDecision | None] = mapped_column(
        Enum(RetryDecision, native_enum=False, create_constraint=True, name="retry_decision")
    )
    operator_review_required: Mapped[bool | None] = mapped_column(Boolean)
    validation_errors: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    diagnosis_schema_version: Mapped[int] = mapped_column(SmallInteger)
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    rule_version: Mapped[int | None] = mapped_column(Integer)
    reused_from_diagnosis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("diagnoses.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncidentRecord(Base):
    """One operational grouping of equivalent active failures."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint(
            "initial_failure_event_id",
            name="uq_incidents_initial_failure_event",
        ),
        Index(
            "uq_incidents_active_correlation",
            "environment",
            "dag_id",
            "task_id",
            "error_signature_id",
            unique=True,
            postgresql_where=text(
                "error_signature_id IS NOT NULL AND status IN ('OPEN', 'ACKNOWLEDGED')"
            ),
            sqlite_where=text(
                "error_signature_id IS NOT NULL AND status IN ('OPEN', 'ACKNOWLEDGED')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(64))
    dag_id: Mapped[str] = mapped_column(String(250))
    task_id: Mapped[str] = mapped_column(String(250))
    error_signature_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("error_signatures.id"))
    initial_failure_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("failure_events.id", ondelete="CASCADE")
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, native_enum=False, create_constraint=True, name="incident_status"),
        default=IncidentStatus.OPEN,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncidentFailureRecord(Base):
    """Membership of exactly one Failure Event in one Incident."""

    __tablename__ = "incident_failure_events"
    __table_args__ = (UniqueConstraint("failure_event_id", name="uq_incident_failure_event"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    failure_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("failure_events.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncidentStateTransitionRecord(Base):
    """Immutable audit record for one Incident state change."""

    __tablename__ = "incident_state_transitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    previous_status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            native_enum=False,
            create_constraint=True,
            name="incident_transition_previous_status",
        )
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            native_enum=False,
            create_constraint=True,
            name="incident_transition_status",
        )
    )
    initiator: Mapped[IncidentTransitionInitiator] = mapped_column(
        Enum(
            IncidentTransitionInitiator,
            native_enum=False,
            create_constraint=True,
            name="incident_transition_initiator",
        )
    )
    actor: Mapped[str] = mapped_column(String(250))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncidentHumanDiagnosisRecord(Base):
    """One immutable operator-published or withdrawn Incident diagnosis revision."""

    __tablename__ = "incident_human_diagnoses"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_incident_human_diagnoses_revision"),
        CheckConstraint(
            "(action = 'PUBLISH' AND classification IS NOT NULL AND root_cause IS NOT NULL "
            "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL) OR "
            "(action = 'WITHDRAW' AND classification IS NULL AND root_cause IS NULL "
            "AND recommended_actions IS NULL AND retry_decision IS NULL "
            "AND basis_diagnosis_id IS NULL)",
            name="ck_incident_human_diagnoses_shape",
        ),
        UniqueConstraint("incident_id", "revision", name="uq_incident_human_diagnoses_revision"),
        Index("ix_incident_human_diagnoses_incident_revision", "incident_id", "revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[HumanDiagnosisAction] = mapped_column(
        Enum(
            HumanDiagnosisAction,
            native_enum=False,
            create_constraint=True,
            name="human_diagnosis_action",
        )
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incident_human_diagnoses.id")
    )
    basis_diagnosis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("diagnoses.id"))
    classification: Mapped[ErrorClassification | None] = mapped_column(
        Enum(
            ErrorClassification,
            native_enum=False,
            create_constraint=True,
            name="error_classification",
        )
    )
    root_cause: Mapped[str | None] = mapped_column(Text)
    recommended_actions: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    retry_decision: Mapped[RetryDecision | None] = mapped_column(
        Enum(RetryDecision, native_enum=False, create_constraint=True, name="retry_decision")
    )
    operator_notes: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_identity: Mapped[str] = mapped_column(String(250))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncidentHumanDiagnosisEvidenceRecord(Base):
    """Sanitized Evidence snapshot selected for a published human diagnosis."""

    __tablename__ = "incident_human_diagnosis_evidence"
    __table_args__ = (
        CheckConstraint("line_id >= 1", name="ck_incident_human_diagnosis_evidence_line"),
        CheckConstraint("position >= 0", name="ck_incident_human_diagnosis_evidence_position"),
        UniqueConstraint(
            "human_diagnosis_id",
            "source_diagnosis_id",
            "line_id",
            name="uq_incident_human_diagnosis_evidence_source",
        ),
        UniqueConstraint(
            "human_diagnosis_id",
            "position",
            name="uq_incident_human_diagnosis_evidence_position",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    human_diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incident_human_diagnoses.id", ondelete="CASCADE")
    )
    source_diagnosis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diagnoses.id"))
    failure_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("failure_events.id"))
    line_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class IncidentRecoveryNotificationRecord(Base):
    """Idempotent delivery state for one Incident recovery notification."""

    __tablename__ = "incident_recovery_notifications"
    __table_args__ = (
        CheckConstraint(
            "delivery_key_version >= 1",
            name="ck_incident_recovery_notification_key_version",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_incident_recovery_notification_attempt_count",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True
    )
    delivery_key: Mapped[str] = mapped_column(String(64), unique=True)
    delivery_key_version: Mapped[int] = mapped_column(SmallInteger)
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        Enum(
            NotificationDeliveryStatus,
            native_enum=False,
            create_constraint=True,
            name="incident_recovery_notification_status",
        ),
        default=NotificationDeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    last_error_category: Mapped[str | None] = mapped_column(String(50))
    last_response_status: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OperationalMetricCounterRecord(Base):
    """Durable low-cardinality counter shared by all service processes."""

    __tablename__ = "operational_metric_counters"
    __table_args__ = (CheckConstraint("value >= 0", name="ck_operational_metric_counter_value"),)

    metric_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    label_value: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyReportRecord(Base):
    """One generated and idempotently delivered UTC daily report."""

    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("report_date", "environment", name="uq_daily_reports_date_environment"),
        CheckConstraint("report_schema_version >= 1", name="ck_daily_reports_schema_version"),
        CheckConstraint("attempt_count >= 0", name="ck_daily_reports_attempt_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_date: Mapped[date] = mapped_column(Date)
    environment: Mapped[str] = mapped_column(String(64))
    report_schema_version: Mapped[int] = mapped_column(SmallInteger)
    statistics: Mapped[dict[str, object]] = mapped_column(JSON)
    rule_based_report: Mapped[dict[str, object]] = mapped_column(JSON)
    ai_summary: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    summary_provider: Mapped[str | None] = mapped_column(String(50))
    delivery_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        Enum(
            NotificationDeliveryStatus,
            native_enum=False,
            create_constraint=True,
            name="daily_report_delivery_status",
        ),
        default=NotificationDeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_category: Mapped[str | None] = mapped_column(String(50))
    last_response_status: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyReportScheduleRecord(Base):
    """One environment's Daily Report automation settings."""

    __tablename__ = "daily_report_schedules"
    __table_args__ = (
        UniqueConstraint("environment", name="uq_daily_report_schedules_environment"),
        CheckConstraint("revision >= 1", name="ck_daily_report_schedules_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    display_name: Mapped[str] = mapped_column(String(120), default="Daily Report")
    report_title: Mapped[str] = mapped_column(String(160), default="DagSentry 일일 장애 리포트")
    notification_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("managed_connections.id", ondelete="SET NULL")
    )
    use_ai_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    run_at_local_time: Mapped[time] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyReportScheduleRunRecord(Base):
    """One scheduled or user-requested Daily Report service invocation."""

    __tablename__ = "daily_report_schedule_runs"
    __table_args__ = (
        CheckConstraint(
            "(trigger_type = 'SCHEDULED' AND schedule_id IS NOT NULL) OR trigger_type = 'MANUAL'",
            name="ck_daily_report_schedule_runs_schedule_shape",
        ),
        UniqueConstraint(
            "schedule_id",
            "report_date",
            "trigger_type",
            name="uq_daily_report_schedule_runs_scheduled_date",
        ),
        Index("ix_daily_report_schedule_runs_status_scheduled_for", "status", "scheduled_for"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("daily_report_schedules.id", ondelete="SET NULL")
    )
    environment: Mapped[str] = mapped_column(String(64))
    report_date: Mapped[date] = mapped_column(Date)
    trigger_type: Mapped[DailyReportScheduleRunTrigger] = mapped_column(
        Enum(
            DailyReportScheduleRunTrigger,
            native_enum=False,
            create_constraint=True,
            name="daily_report_schedule_run_trigger",
        )
    )
    status: Mapped[DailyReportScheduleRunStatus] = mapped_column(
        Enum(
            DailyReportScheduleRunStatus,
            native_enum=False,
            create_constraint=True,
            name="daily_report_schedule_run_status",
        ),
        default=DailyReportScheduleRunStatus.CLAIMED,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("daily_reports.id", ondelete="SET NULL")
    )
    error_category: Mapped[str | None] = mapped_column(String(50))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SchedulerHeartbeatRecord(Base):
    """Latest liveness signal from the dedicated scheduler process."""

    __tablename__ = "scheduler_heartbeats"

    scheduler_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[str] = mapped_column(String(64))


class NotificationDeliveryRecord(Base):
    """Idempotent delivery state and payload snapshot for one effective Diagnosis."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint("delivery_key_version >= 1", name="ck_notification_delivery_key_version"),
        CheckConstraint("attempt_count >= 0", name="ck_notification_delivery_attempt_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diagnoses.id", ondelete="CASCADE"), unique=True
    )
    delivery_key: Mapped[str] = mapped_column(String(64), unique=True)
    delivery_key_version: Mapped[int] = mapped_column(SmallInteger)
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        Enum(
            NotificationDeliveryStatus,
            native_enum=False,
            create_constraint=True,
            name="notification_delivery_status",
        ),
        default=NotificationDeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    suppression_reason: Mapped[str | None] = mapped_column(String(50))
    last_error_category: Mapped[str | None] = mapped_column(String(50))
    last_response_status: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
