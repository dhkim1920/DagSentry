"""Microsoft Teams Workflows Notification Provider."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from dagsentry.config import Settings
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload

TeamsPayload = NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload


@dataclass(frozen=True)
class TeamsProviderConfig:
    """Teams Workflows Webhook URL and bounded retry settings."""

    webhook_url: str
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Teams Webhook URL must be an HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

    def __repr__(self) -> str:
        return (
            "TeamsProviderConfig(webhook_url='**********', "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class TeamsNotificationProvider:
    """Send non-interactive Adaptive Cards through a Teams Workflow Webhook."""

    name = "teams"

    def __init__(
        self,
        config: TeamsProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> TeamsNotificationProvider:
        """Build the explicitly configured Teams Provider."""
        if settings.notification_provider != "teams":
            raise ValueError("Teams Notification Provider is not enabled")
        if settings.teams_webhook_url is None:
            raise ValueError("Teams Webhook URL must be configured")
        return cls(
            TeamsProviderConfig(
                webhook_url=settings.teams_webhook_url,
                timeout_seconds=settings.teams_timeout_seconds,
                max_attempts=settings.teams_max_attempts,
                retry_backoff_seconds=settings.teams_retry_backoff_seconds,
            )
        )

    def send(self, payload: TeamsPayload, *, delivery_key: str) -> int:
        """Post one Adaptive Card with bounded retries."""
        body = _message_body(payload, delivery_key)
        for attempt in range(self.config.max_attempts):
            response: httpx.Response | None = None
            error: NotificationProviderError | None = None
            try:
                response = self.http_client.post(
                    self.config.webhook_url,
                    headers={"Content-Type": "application/json"},
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException:
                error = NotificationProviderError(
                    "Teams request timed out",
                    category=NotificationErrorCategory.TIMEOUT,
                    retryable=True,
                )
            except httpx.TransportError:
                error = NotificationProviderError(
                    "Teams is unavailable",
                    category=NotificationErrorCategory.UNAVAILABLE,
                    retryable=True,
                )
            else:
                error = _response_error(response)
                if error is None:
                    return response.status_code

            assert error is not None
            if error.retryable and attempt + 1 < self.config.max_attempts:
                self.sleep(_retry_delay(response, self.config))
                continue
            raise error

        raise RuntimeError("unreachable Teams request state")  # pragma: no cover


def _response_error(response: httpx.Response) -> NotificationProviderError | None:
    status = response.status_code
    if 200 <= status < 300:
        return None
    if status == 401:
        return _provider_error(NotificationErrorCategory.AUTHENTICATION, False, status)
    if status == 403:
        return _provider_error(NotificationErrorCategory.AUTHORIZATION, False, status)
    if status == 429:
        return _provider_error(NotificationErrorCategory.RATE_LIMITED, True, status)
    if status == 408 or status >= 500:
        return _provider_error(NotificationErrorCategory.UNAVAILABLE, True, status)
    return _provider_error(NotificationErrorCategory.INVALID_REQUEST, False, status)


def _provider_error(
    category: NotificationErrorCategory,
    retryable: bool,
    response_status: int,
) -> NotificationProviderError:
    return NotificationProviderError(
        f"Teams notification failed: {category.value.lower()}",
        category=category,
        retryable=retryable,
        response_status=response_status,
    )


def _retry_delay(response: httpx.Response | None, config: TeamsProviderConfig) -> float:
    if response is None or response.status_code != 429:
        return config.retry_backoff_seconds
    raw_delay = response.headers.get("Retry-After")
    if raw_delay is None:
        return config.retry_backoff_seconds
    try:
        return min(60.0, max(0.0, float(raw_delay)))
    except ValueError:
        return config.retry_backoff_seconds


def _message_body(payload: TeamsPayload, delivery_key: str) -> dict[str, object]:
    if isinstance(payload, NotificationPayload):
        card_body = _diagnosis_card(payload)
    elif isinstance(payload, RecoveryNotificationPayload):
        card_body = _recovery_card(payload)
    else:
        card_body = _daily_report_card(payload)
    card_body.append(_text(f"DagSentry delivery: {delivery_key}", subtle=True, size="Small"))
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": card_body,
                },
            }
        ],
    }


def _diagnosis_card(payload: NotificationPayload) -> list[dict[str, object]]:
    root_cause = payload.root_cause or "No root cause available"
    evidence = "\n".join(f"L{item.line_id}: {item.text}" for item in payload.evidence)
    actions = "\n".join(f"• {item}" for item in payload.recommended_actions)
    facts = [
        _fact("Classification", payload.classification.value),
        _fact("Environment", payload.environment),
        _fact("DAG", payload.dag_id),
        _fact("Task", f"{payload.task_id} · map {payload.map_index} · try {payload.try_number}"),
        _fact("DAG run", payload.dag_run_id),
        _fact("Failed at", payload.failed_at.isoformat()),
        _fact(
            "Incident",
            f"{payload.incident_id} · {payload.incident_status.value} · "
            f"{payload.incident_failure_count} failure(s)",
        ),
        _fact(
            "Diagnosis",
            f"{payload.diagnosis_source.value} · confidence {payload.confidence:.2f} · "
            f"retry {payload.retry_decision.value}",
        ),
        _fact("Failure event", str(payload.failure_event_id)),
        _fact("Diagnosis ID", str(payload.diagnosis_id)),
        _fact("Error signature", payload.error_signature or "Not available"),
    ]
    body: list[dict[str, object]] = [
        _text(
            f"DagSentry {payload.classification.value} · {payload.dag_id}.{payload.task_id}",
            weight="Bolder",
            size="Large",
            color="Attention",
        ),
        {"type": "FactSet", "facts": facts},
        _section("Root cause", root_cause),
        _section("Evidence", evidence or "No evidence"),
        _section("Recommended actions", actions or "No recommended actions"),
    ]
    if payload.airflow_log_url is not None:
        body.append(_section("Airflow log", payload.airflow_log_url))
    return body


def _recovery_card(payload: RecoveryNotificationPayload) -> list[dict[str, object]]:
    return [
        _text("Incident recovered", weight="Bolder", size="Large", color="Good"),
        _text(f"{payload.dag_id}.{payload.task_id} is healthy again."),
        {
            "type": "FactSet",
            "facts": [
                _fact("Environment", payload.environment),
                _fact("Incident", str(payload.incident_id)),
                _fact("Linked failures", str(payload.incident_failure_count)),
                _fact("Recovered at", payload.recovered_at.isoformat()),
            ],
        },
    ]


def _daily_report_card(payload: DailyReportNotificationPayload) -> list[dict[str, object]]:
    report = payload.rule_based_report
    statistics = payload.statistics
    body: list[dict[str, object]] = [
        _text(report.title, weight="Bolder", size="Large", color="Accent"),
        _text(report.overview),
        {
            "type": "FactSet",
            "facts": [
                _fact("Date", statistics.report_date.isoformat()),
                _fact("Environment", statistics.environment),
                _fact("Failure attempts", str(statistics.failure_attempts)),
                _fact("Affected tasks", str(statistics.affected_task_instances)),
                _fact("Affected DAG runs", str(statistics.affected_dag_runs)),
            ],
        },
        _section("Highlights", "\n".join(f"• {item}" for item in report.highlights)),
        _section("Priorities", "\n".join(f"• {item}" for item in report.priorities)),
    ]
    if payload.ai_summary is not None:
        body.append(
            _section(
                "AI narrative",
                "\n".join(payload.ai_summary.key_changes + payload.ai_summary.priorities),
            )
        )
    return body


def _fact(title: str, value: str) -> dict[str, str]:
    return {"title": _truncate(title, 100), "value": _truncate(value, 500)}


def _section(title: str, value: str) -> dict[str, object]:
    return _text(f"**{title}**\n{value}")


def _text(
    text: str,
    *,
    weight: str | None = None,
    size: str | None = None,
    color: str | None = None,
    subtle: bool = False,
) -> dict[str, object]:
    block: dict[str, object] = {"type": "TextBlock", "text": _truncate(text, 2_000), "wrap": True}
    if weight is not None:
        block["weight"] = weight
    if size is not None:
        block["size"] = size
    if color is not None:
        block["color"] = color
    if subtle:
        block["isSubtle"] = True
    return block


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
