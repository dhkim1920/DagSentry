"""Durable, low-cardinality Prometheus operational metrics."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dagsentry.db import SessionFactory
from dagsentry.domain.incident import IncidentStatus
from dagsentry.models import (
    DiagnosisOutboxRecord,
    IncidentRecord,
    OperationalMetricCounterRecord,
    OutboxStatus,
)

logger = logging.getLogger(__name__)
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(frozen=True)
class CounterDefinition:
    help: str
    label_name: str
    label_values: tuple[str, ...]


COUNTERS: dict[str, CounterDefinition] = {
    "dagsentry_ingest_events_total": CounterDefinition(
        "Failure Ingest requests by durable outcome.",
        "result",
        ("created", "duplicate", "failure"),
    ),
    "dagsentry_worker_jobs_total": CounterDefinition(
        "Diagnosis Worker job transitions.",
        "result",
        ("completed", "retry", "dead", "stale_reclaimed"),
    ),
    "dagsentry_log_collection_total": CounterDefinition(
        "Airflow Task log collection outcomes.",
        "result",
        ("available", "unavailable"),
    ),
    "dagsentry_diagnosis_outcomes_total": CounterDefinition(
        "AI, reuse, rejection, and Rule fallback diagnosis outcomes.",
        "result",
        (
            "ai_success",
            "reused",
            "evidence_rejected",
            "provider_error",
            "provider_not_configured",
            "log_unavailable",
        ),
    ),
    "dagsentry_notification_attempts_total": CounterDefinition(
        "Notification Provider delivery attempts.",
        "result",
        ("delivered", "failed"),
    ),
    "dagsentry_incident_events_total": CounterDefinition(
        "Incident lifecycle events.",
        "event",
        ("opened", "recovered"),
    ),
    "dagsentry_recovery_checker_runs_total": CounterDefinition(
        "Recovery Checker executions.",
        "result",
        ("success", "degraded"),
    ),
    "dagsentry_auth_events_total": CounterDefinition(
        "Local user authentication and session events.",
        "event",
        ("login_success", "login_failure", "login_rate_limited", "logout", "password_changed"),
    ),
}


def increment_counter(
    session: Session,
    metric_name: str,
    label_value: str,
    *,
    amount: int = 1,
    now: datetime | None = None,
) -> None:
    """Atomically increment one allowlisted counter in an existing transaction."""
    definition = COUNTERS.get(metric_name)
    if definition is None:
        raise ValueError(f"Unknown operational counter: {metric_name}")
    if label_value not in definition.label_values:
        raise ValueError(f"Invalid label value for {metric_name}: {label_value}")
    if amount < 1:
        raise ValueError("counter increment amount must be positive")
    updated_at = _as_utc(now or datetime.now(UTC))
    values = {
        "metric_name": metric_name,
        "label_value": label_value,
        "value": amount,
        "updated_at": updated_at,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        pg_statement = postgresql_insert(OperationalMetricCounterRecord).values(**values)
        session.execute(
            pg_statement.on_conflict_do_update(
                index_elements=["metric_name", "label_value"],
                set_={
                    "value": OperationalMetricCounterRecord.value + amount,
                    "updated_at": updated_at,
                },
            )
        )
    elif dialect == "sqlite":
        sqlite_statement = sqlite_insert(OperationalMetricCounterRecord).values(**values)
        session.execute(
            sqlite_statement.on_conflict_do_update(
                index_elements=["metric_name", "label_value"],
                set_={
                    "value": OperationalMetricCounterRecord.value + amount,
                    "updated_at": updated_at,
                },
            )
        )
    else:  # pragma: no cover - only configured dialects are supported
        raise RuntimeError(f"Unsupported database dialect: {dialect}")


def record_counter(
    session_factory: SessionFactory,
    metric_name: str,
    label_value: str,
    *,
    amount: int = 1,
    now: datetime | None = None,
) -> None:
    """Record a metric without allowing observability failure to break business work."""
    try:
        with session_factory.begin() as session:
            increment_counter(
                session,
                metric_name,
                label_value,
                amount=amount,
                now=now,
            )
    except SQLAlchemyError:
        logger.exception("operational metric update failed")


def render_prometheus_metrics(
    session_factory: SessionFactory,
    *,
    now: datetime | None = None,
) -> str:
    """Render durable counters and current database gauges in Prometheus text format."""
    current_time = _as_utc(now or datetime.now(UTC))
    with session_factory() as session:
        counter_rows = session.scalars(select(OperationalMetricCounterRecord)).all()
        outbox_counts: dict[OutboxStatus, int] = {
            status: count
            for status, count in session.execute(
                select(DiagnosisOutboxRecord.status, func.count()).group_by(
                    DiagnosisOutboxRecord.status
                )
            ).tuples()
        }
        oldest_ready = session.scalar(
            select(func.min(DiagnosisOutboxRecord.available_at)).where(
                DiagnosisOutboxRecord.status == OutboxStatus.PENDING,
                DiagnosisOutboxRecord.available_at <= current_time,
            )
        )
        incident_counts: dict[IncidentStatus, int] = {
            status: count
            for status, count in session.execute(
                select(IncidentRecord.status, func.count()).group_by(IncidentRecord.status)
            ).tuples()
        }
        last_checker_run = session.scalar(
            select(func.max(OperationalMetricCounterRecord.updated_at)).where(
                OperationalMetricCounterRecord.metric_name
                == "dagsentry_recovery_checker_runs_total"
            )
        )

    stored = {(row.metric_name, row.label_value): row.value for row in counter_rows}
    lines: list[str] = []
    for metric_name, definition in COUNTERS.items():
        lines.extend((f"# HELP {metric_name} {definition.help}", f"# TYPE {metric_name} counter"))
        for label_value in definition.label_values:
            value = stored.get((metric_name, label_value), 0)
            lines.append(f'{metric_name}{{{definition.label_name}="{label_value}"}} {value}')

    lines.extend(
        (
            "# HELP dagsentry_outbox_jobs Current Diagnosis Outbox jobs by status.",
            "# TYPE dagsentry_outbox_jobs gauge",
        )
    )
    for outbox_status in OutboxStatus:
        lines.append(
            f'dagsentry_outbox_jobs{{status="{outbox_status.value}"}} '
            f"{outbox_counts.get(outbox_status, 0)}"
        )
    lines.extend(
        (
            "# HELP dagsentry_outbox_oldest_ready_age_seconds Age of the oldest ready job.",
            "# TYPE dagsentry_outbox_oldest_ready_age_seconds gauge",
            "dagsentry_outbox_oldest_ready_age_seconds "
            f"{_age_seconds(current_time, oldest_ready, empty=0.0)}",
            "# HELP dagsentry_incidents Current Incidents by lifecycle status.",
            "# TYPE dagsentry_incidents gauge",
        )
    )
    for incident_status in IncidentStatus:
        lines.append(
            f'dagsentry_incidents{{status="{incident_status.value}"}} '
            f"{incident_counts.get(incident_status, 0)}"
        )
    lines.extend(
        (
            "# HELP dagsentry_recovery_checker_delay_seconds "
            "Seconds since the latest completed Recovery Checker run.",
            "# TYPE dagsentry_recovery_checker_delay_seconds gauge",
            "dagsentry_recovery_checker_delay_seconds "
            f"{_format_number(_age_seconds(current_time, last_checker_run, empty=math.nan))}",
        )
    )
    return "\n".join(lines) + "\n"


def _age_seconds(current: datetime, value: datetime | None, *, empty: float) -> float:
    if value is None:
        return empty
    return max(0.0, (current - _as_utc(value)).total_seconds())


def _format_number(value: float) -> str:
    return "NaN" if math.isnan(value) else str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
