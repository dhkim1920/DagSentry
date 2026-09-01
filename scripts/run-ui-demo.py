#!/usr/bin/env python3
"""Seed a disposable SQLite database and run the DagSentry UI locally."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import Base, create_session_factory
from dagsentry.domain.diagnosis import (
    DiagnosisSource,
    DiagnosisValidationStatus,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureState
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.identity import bootstrap_admin
from dagsentry.models import (
    DiagnosisRecord,
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
    IncidentStateTransitionRecord,
    NotificationDeliveryRecord,
)
from dagsentry.scheduler import DailyReportScheduler

DEMO_DATABASE_PATH = Path(tempfile.gettempdir()) / "dagsentry-ui-demo.sqlite3"
DEMO_EMAIL = "admin@dagsentry.local"
DEMO_PASSWORD = "DagSentry-demo-2026!"
DEFAULT_DEMO_HOST = "127.0.0.1"
DEMO_PORT = 8000

SIGNATURE_SPECS = (
    (
        "ConnectionError",
        "ECONNREFUSED",
        "Connection refused on port <PORT>",
        "_PythonDecoratedOperator",
        ErrorClassification.NETWORK,
    ),
    (
        "AirflowException",
        None,
        "Task failed with exception in decorated operator",
        "_PythonDecoratedOperator",
        ErrorClassification.DAG_CODE,
    ),
    (
        "OOMKilled",
        "OOM_KILLED",
        "Container was killed due to out-of-memory",
        "KubernetesPodOperator",
        ErrorClassification.RESOURCE,
    ),
    (
        "AccessDenied",
        "AccessDenied",
        "Access denied while reading object storage",
        "S3Hook",
        ErrorClassification.AUTHORIZATION,
    ),
    (
        "DeadlockDetected",
        "40P01",
        "PostgreSQL deadlock detected",
        "PostgresHook",
        ErrorClassification.SOURCE_DATABASE,
    ),
)

INCIDENT_STATUSES = (
    IncidentStatus.OPEN,
    IncidentStatus.OPEN,
    IncidentStatus.ACKNOWLEDGED,
    IncidentStatus.RECOVERED,
    IncidentStatus.RESOLVED,
    IncidentStatus.IGNORED,
    IncidentStatus.OPEN,
    IncidentStatus.ACKNOWLEDGED,
    IncidentStatus.RECOVERED,
    IncidentStatus.RESOLVED,
)

DAGS = (
    ("orders_pipeline", "load_orders"),
    ("payments_pipeline", "transfer_data"),
    ("customer_sync", "sync_customers"),
    ("daily_reporting", "generate_report"),
    ("analytics_pipeline", "upsert_metrics"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="replace the existing disposable demo database before starting",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_DEMO_HOST,
        help="server bind address; use 0.0.0.0 for access from the local network",
    )
    parser.add_argument(
        "--with-scheduler",
        action="store_true",
        help="run the Daily Report scheduler with the demo database",
    )
    return parser.parse_args()


def database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def seed_demo_database(path: Path) -> None:
    engine = create_engine(database_url(path))
    Base.metadata.create_all(engine)
    now = datetime.now(UTC).replace(microsecond=0)

    with Session(engine, expire_on_commit=False) as session:
        bootstrap_admin(
            session,
            email=DEMO_EMAIL,
            display_name="UI Demo Admin",
            password=DEMO_PASSWORD,
            now=now - timedelta(days=30),
        )
        signatures = create_signatures(session, now)
        create_incidents_and_diagnoses(session, signatures, now)
        session.commit()

    engine.dispose()


def ensure_demo_schema(path: Path) -> None:
    """Create tables introduced after an existing disposable demo DB was seeded."""
    engine = create_engine(database_url(path))
    Base.metadata.create_all(engine)
    existing_columns = {
        column["name"] for column in inspect(engine).get_columns("daily_report_schedules")
    }
    additions = {
        "display_name": "VARCHAR(120) NOT NULL DEFAULT 'Daily Report'",
        "report_title": "VARCHAR(160) NOT NULL DEFAULT 'DagSentry 일일 장애 리포트'",
        "notification_connection_id": "CHAR(32)",
        "use_ai_summary": "BOOLEAN NOT NULL DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE daily_report_schedules ADD COLUMN {name} {definition}")
                )
    engine.dispose()


def create_signatures(session: Session, now: datetime) -> list[ErrorSignatureRecord]:
    signatures: list[ErrorSignatureRecord] = []
    for index, (exception, vendor, message, operator, _classification) in enumerate(
        SIGNATURE_SPECS
    ):
        signature = ErrorSignatureRecord(
            fingerprint_version=1,
            fingerprint=hashlib.sha256(f"ui-demo-signature-{index}".encode()).hexdigest(),
            operator_type=operator,
            exception_class=exception,
            vendor_error_code=vendor,
            normalized_message=message,
            application_stack_frame=f"/opt/airflow/dags/demo_pipeline_{index}.py:42",
            created_at=now - timedelta(days=14 - index),
        )
        session.add(signature)
        signatures.append(signature)
    session.flush()
    return signatures


def create_incidents_and_diagnoses(
    session: Session,
    signatures: list[ErrorSignatureRecord],
    now: datetime,
) -> None:
    original_ai: DiagnosisRecord | None = None
    delivery_index = 0

    for incident_index, status in enumerate(INCIDENT_STATUSES):
        signature_index = incident_index % len(signatures)
        signature = signatures[signature_index]
        classification = SIGNATURE_SPECS[signature_index][4]
        dag_id, task_id = DAGS[signature_index]
        failures = create_failures(
            session,
            incident_index=incident_index,
            signature=signature,
            dag_id=dag_id,
            task_id=task_id,
            now=now,
        )
        incident = create_incident(session, failures, signature, status)
        link_failures(session, incident, failures)
        create_transition(session, incident, failures[-1].observed_at, status)

        if incident_index == 2:
            session.add(rejected_diagnosis(failures[0], signature, classification))

        diagnosis = create_diagnosis(
            incident_index=incident_index,
            failure=failures[0],
            signature=signature,
            classification=classification,
            original_ai=original_ai,
        )
        session.add(diagnosis)
        session.flush()
        if original_ai is None and diagnosis.source == DiagnosisSource.AI:
            original_ai = diagnosis

        delivery_index += 1
        session.add(delivery_for(diagnosis, failures[0], delivery_index))


def create_failures(
    session: Session,
    *,
    incident_index: int,
    signature: ErrorSignatureRecord,
    dag_id: str,
    task_id: str,
    now: datetime,
) -> list[FailureEventRecord]:
    failure_count = incident_index % 4 + 1
    failures: list[FailureEventRecord] = []
    for try_index in range(failure_count):
        observed_at = now - timedelta(
            days=incident_index % 7,
            hours=incident_index * 2,
            minutes=(failure_count - try_index) * 7,
        )
        failure = FailureEventRecord(
            event_key=hashlib.sha256(
                f"ui-demo-failure-{incident_index}-{try_index}".encode()
            ).hexdigest(),
            event_key_version=1,
            environment=("production", "staging", "dev")[incident_index % 3],
            dag_id=dag_id,
            dag_run_id=f"scheduled__{observed_at.isoformat()}",
            task_id=task_id,
            map_index=-1,
            try_number=try_index + 1,
            source=CollectionSource.LISTENER,
            state=FailureState.FAILED,
            observed_at=observed_at,
            operator_type=signature.operator_type,
            created_at=observed_at,
        )
        session.add(failure)
        failures.append(failure)
    session.flush()
    return failures


def create_incident(
    session: Session,
    failures: list[FailureEventRecord],
    signature: ErrorSignatureRecord,
    status: IncidentStatus,
) -> IncidentRecord:
    incident = IncidentRecord(
        environment=failures[0].environment,
        dag_id=failures[0].dag_id,
        task_id=failures[0].task_id,
        error_signature_id=signature.id,
        initial_failure_event_id=failures[0].id,
        status=status,
        created_at=failures[0].observed_at,
        updated_at=failures[-1].observed_at,
    )
    session.add(incident)
    session.flush()
    return incident


def link_failures(
    session: Session,
    incident: IncidentRecord,
    failures: list[FailureEventRecord],
) -> None:
    for failure in failures:
        session.add(
            IncidentFailureRecord(
                incident_id=incident.id,
                failure_event_id=failure.id,
                created_at=failure.observed_at,
            )
        )


def create_transition(
    session: Session,
    incident: IncidentRecord,
    observed_at: datetime,
    status: IncidentStatus,
) -> None:
    if status == IncidentStatus.OPEN:
        return
    recovered = status == IncidentStatus.RECOVERED
    session.add(
        IncidentStateTransitionRecord(
            incident_id=incident.id,
            previous_status=IncidentStatus.OPEN,
            status=status,
            initiator=(
                IncidentTransitionInitiator.SYSTEM
                if recovered
                else IncidentTransitionInitiator.OPERATOR
            ),
            actor="recovery-checker" if recovered else DEMO_EMAIL,
            reason="Task recovered" if recovered else "Demo transition",
            created_at=observed_at + timedelta(minutes=5),
        )
    )


def rejected_diagnosis(
    failure: FailureEventRecord,
    signature: ErrorSignatureRecord,
    classification: ErrorClassification,
) -> DiagnosisRecord:
    return DiagnosisRecord(
        failure_event_id=failure.id,
        error_signature_id=signature.id,
        source=DiagnosisSource.AI,
        validation_status=DiagnosisValidationStatus.REJECTED,
        classification=classification,
        root_cause="AI output failed evidence validation.",
        confidence=0.61,
        confidence_reason="The provider response was incomplete.",
        matched_rule=None,
        extracted_values=[],
        evidence=[{"line_id": 25, "text": "Evidence reference was invalid"}],
        recommended_actions=["Review the validated Rule fallback"],
        retry_decision=RetryDecision.UNKNOWN,
        operator_review_required=True,
        validation_errors=["EVIDENCE_LINE_NOT_FOUND"],
        diagnosis_schema_version=1,
        prompt_version="ai-diagnosis-v1",
        rule_version=None,
        reused_from_diagnosis_id=None,
        created_at=failure.observed_at + timedelta(minutes=1),
    )


def create_diagnosis(
    *,
    incident_index: int,
    failure: FailureEventRecord,
    signature: ErrorSignatureRecord,
    classification: ErrorClassification,
    original_ai: DiagnosisRecord | None,
) -> DiagnosisRecord:
    if incident_index == 5 and original_ai is not None:
        return DiagnosisRecord(
            failure_event_id=failure.id,
            error_signature_id=signature.id,
            source=DiagnosisSource.REUSED,
            validation_status=DiagnosisValidationStatus.PASSED,
            classification=None,
            root_cause=None,
            confidence=None,
            confidence_reason=None,
            matched_rule=None,
            extracted_values=None,
            evidence=None,
            recommended_actions=None,
            retry_decision=None,
            operator_review_required=None,
            validation_errors=None,
            diagnosis_schema_version=1,
            prompt_version="ai-diagnosis-v1",
            rule_version=None,
            reused_from_diagnosis_id=original_ai.id,
            created_at=failure.observed_at + timedelta(minutes=2),
        )

    source = DiagnosisSource.AI if incident_index % 2 == 0 else DiagnosisSource.RULE
    return DiagnosisRecord(
        failure_event_id=failure.id,
        error_signature_id=signature.id,
        source=source,
        validation_status=DiagnosisValidationStatus.PASSED,
        classification=classification,
        root_cause=f"{signature.exception_class}: {signature.normalized_message.lower()}",
        confidence=0.85 if source == DiagnosisSource.AI else 0.92,
        confidence_reason=(
            "Exception chain and network refusal evidence agree."
            if source == DiagnosisSource.AI
            else "Deterministic rule matched canonical fields."
        ),
        matched_rule=None if source == DiagnosisSource.AI else "network.connection_refused",
        extracted_values=[{"name": "port", "value": "6543"}],
        evidence=[
            {
                "line_id": 25,
                "text": (
                    f"{signature.exception_class}: warehouse gateway refused connection "
                    "on port 6543"
                ),
            },
            {
                "line_id": 24,
                "text": "raise requests.exceptions.ConnectionError(message)",
            },
            {
                "line_id": 23,
                "text": "NewConnectionError: [Errno 111] Connection refused",
            },
        ],
        recommended_actions=[
            "Check the target service health.",
            "Confirm port 6543 is listening.",
            "Verify worker network connectivity.",
            "Review NetworkPolicy and firewall rules.",
        ],
        retry_decision=RetryDecision.RETRYABLE,
        operator_review_required=False,
        validation_errors=[],
        diagnosis_schema_version=1,
        prompt_version="ai-diagnosis-v1" if source == DiagnosisSource.AI else None,
        rule_version=1 if source == DiagnosisSource.RULE else None,
        reused_from_diagnosis_id=None,
        created_at=failure.observed_at + timedelta(minutes=2),
    )


def delivery_for(
    diagnosis: DiagnosisRecord,
    failure: FailureEventRecord,
    delivery_index: int,
) -> NotificationDeliveryRecord:
    return NotificationDeliveryRecord(
        diagnosis_id=diagnosis.id,
        delivery_key=hashlib.sha256(f"ui-demo-delivery-{delivery_index}".encode()).hexdigest(),
        delivery_key_version=1,
        provider="SLACK",
        status=NotificationDeliveryStatus.DELIVERED,
        attempt_count=1,
        payload={
            "airflow_log_url": (
                f"http://localhost:8080/dags/{failure.dag_id}/runs/{failure.dag_run_id}"
            )
        },
        suppression_reason=None,
        last_error_category=None,
        last_response_status=200,
        delivered_at=diagnosis.created_at + timedelta(seconds=2),
        created_at=diagnosis.created_at,
        updated_at=diagnosis.created_at + timedelta(seconds=2),
    )


def main() -> None:
    args = parse_args()
    if args.reset and DEMO_DATABASE_PATH.exists():
        DEMO_DATABASE_PATH.unlink()
    if not DEMO_DATABASE_PATH.exists():
        seed_demo_database(DEMO_DATABASE_PATH)
    else:
        ensure_demo_schema(DEMO_DATABASE_PATH)

    url_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{url_host}:{DEMO_PORT}/ui/"
    print("DagSentry UI demo")
    print(f"  URL:      {url}")
    print(f"  Email:    {DEMO_EMAIL}")
    print(f"  Password: {DEMO_PASSWORD}")
    print(f"  Database: {DEMO_DATABASE_PATH}")
    if args.host == "0.0.0.0":
        print(f"  LAN:      http://<this-machine-ip>:{DEMO_PORT}/ui/")
    print("  Reset:    uv run python scripts/run-ui-demo.py --reset")

    settings = Settings(database_url=database_url(DEMO_DATABASE_PATH), environment="demo")
    application = create_app(settings, create_session_factory(settings.database_url))
    scheduler: DailyReportScheduler | None = None
    scheduler_thread: threading.Thread | None = None
    if args.with_scheduler:
        scheduler = DailyReportScheduler(
            settings,
            create_session_factory(settings.database_url),
        )
        scheduler_thread = threading.Thread(
            target=scheduler.run_forever,
            name="dagsentry-demo-scheduler",
            daemon=True,
        )
        scheduler_thread.start()
        print("  Scheduler: Daily Report scheduler enabled")
    try:
        uvicorn.run(application, host=args.host, port=DEMO_PORT, log_config=None)
    finally:
        if scheduler is not None:
            scheduler.shutdown()
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=5)


if __name__ == "__main__":
    main()
