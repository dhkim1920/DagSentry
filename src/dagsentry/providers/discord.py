"""Discord Incoming Webhook Notification Provider."""

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

DiscordPayload = NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload


@dataclass(frozen=True)
class DiscordProviderConfig:
    """Discord Incoming Webhook URL and bounded retry settings."""

    webhook_url: str
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Discord Webhook URL must be an HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

    def __repr__(self) -> str:
        return (
            "DiscordProviderConfig(webhook_url='**********', "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class DiscordNotificationProvider:
    """Send mention-safe embeds through a Discord Incoming Webhook."""

    name = "discord"

    def __init__(
        self,
        config: DiscordProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> DiscordNotificationProvider:
        """Build the explicitly configured Discord Provider."""
        if settings.notification_provider != "discord":
            raise ValueError("Discord Notification Provider is not enabled")
        if settings.discord_webhook_url is None:
            raise ValueError("Discord Webhook URL must be configured")
        return cls(
            DiscordProviderConfig(
                webhook_url=settings.discord_webhook_url,
                timeout_seconds=settings.discord_timeout_seconds,
                max_attempts=settings.discord_max_attempts,
                retry_backoff_seconds=settings.discord_retry_backoff_seconds,
            )
        )

    def send(self, payload: DiscordPayload, *, delivery_key: str) -> int:
        """Execute one Webhook with server-side save confirmation and bounded retries."""
        body = _message_body(payload, delivery_key)
        for attempt in range(self.config.max_attempts):
            response: httpx.Response | None = None
            error: NotificationProviderError | None = None
            try:
                response = self.http_client.post(
                    httpx.URL(self.config.webhook_url).copy_merge_params({"wait": "true"}),
                    headers={"Content-Type": "application/json"},
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException:
                error = NotificationProviderError(
                    "Discord request timed out",
                    category=NotificationErrorCategory.TIMEOUT,
                    retryable=True,
                )
            except httpx.TransportError:
                error = NotificationProviderError(
                    "Discord is unavailable",
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

        raise RuntimeError("unreachable Discord request state")  # pragma: no cover


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
        f"Discord notification failed: {category.value.lower()}",
        category=category,
        retryable=retryable,
        response_status=response_status,
    )


def _retry_delay(response: httpx.Response | None, config: DiscordProviderConfig) -> float:
    if response is None or response.status_code != 429:
        return config.retry_backoff_seconds
    raw_delay: object = response.headers.get("Retry-After")
    if raw_delay is None:
        try:
            body = response.json()
        except ValueError:
            body = None
        raw_delay = body.get("retry_after") if isinstance(body, dict) else None
    if not isinstance(raw_delay, (str, int, float)) or isinstance(raw_delay, bool):
        return config.retry_backoff_seconds
    try:
        return min(60.0, max(0.0, float(raw_delay)))
    except ValueError:
        return config.retry_backoff_seconds


def _message_body(payload: DiscordPayload, delivery_key: str) -> dict[str, object]:
    if isinstance(payload, NotificationPayload):
        content, embed = _diagnosis_message(payload)
    elif isinstance(payload, RecoveryNotificationPayload):
        content, embed = _recovery_message(payload)
    else:
        content, embed = _daily_report_message(payload)
    embed["footer"] = {"text": _truncate(f"DagSentry delivery: {delivery_key}", 2_048)}
    return {
        "content": _truncate(content, 2_000),
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }


def _diagnosis_message(payload: NotificationPayload) -> tuple[str, dict[str, object]]:
    root_cause = payload.root_cause or "No root cause available"
    evidence = "\n".join(f"L{item.line_id}: {item.text}" for item in payload.evidence)
    actions = "\n".join(payload.recommended_actions) or "No recommended actions"
    content = (
        f"DagSentry {payload.classification.value}: {payload.dag_id}.{payload.task_id} failed. "
        f"Root cause: {root_cause}"
    )
    embed: dict[str, object] = {
        "title": _truncate(f"{payload.classification.value} · {payload.dag_id}", 256),
        "description": _truncate(root_cause, 2_000),
        "color": 0xD83C3E,
        "fields": [
            _field(
                "Task", f"{payload.task_id} · map {payload.map_index} · try {payload.try_number}"
            ),
            _field("Environment", payload.environment),
            _field("DAG run", payload.dag_run_id),
            _field("Failed at", payload.failed_at.isoformat()),
            _field(
                "Incident",
                f"{payload.incident_id} · {payload.incident_status.value} · "
                f"{payload.incident_failure_count} failure(s)",
            ),
            _field(
                "Diagnosis",
                f"{payload.diagnosis_source.value} · confidence {payload.confidence:.2f} · "
                f"retry {payload.retry_decision.value}",
            ),
            _field("Evidence", evidence or "No evidence"),
            _field("Recommended actions", actions),
            _field("Failure event", str(payload.failure_event_id)),
            _field("Diagnosis ID", str(payload.diagnosis_id)),
            _field("Error signature", payload.error_signature or "Not available"),
        ],
    }
    if payload.airflow_log_url is not None:
        fields = embed["fields"]
        assert isinstance(fields, list)
        fields.append(_field("Airflow log", payload.airflow_log_url))
    return content, embed


def _recovery_message(payload: RecoveryNotificationPayload) -> tuple[str, dict[str, object]]:
    content = f"DagSentry recovered: {payload.dag_id}.{payload.task_id}"
    return content, {
        "title": "Incident recovered",
        "description": _truncate(content, 2_000),
        "color": 0x2EB886,
        "fields": [
            _field("Environment", payload.environment),
            _field("Incident", str(payload.incident_id)),
            _field("Linked failures", str(payload.incident_failure_count)),
            _field("Recovered at", payload.recovered_at.isoformat()),
        ],
    }


def _daily_report_message(payload: DailyReportNotificationPayload) -> tuple[str, dict[str, object]]:
    report = payload.rule_based_report
    fields = [
        _field("Overview", report.overview),
        _field("Highlights", "\n".join(report.highlights)),
        _field("Priorities", "\n".join(report.priorities)),
    ]
    if payload.ai_summary is not None:
        fields.append(
            _field(
                "AI narrative",
                "\n".join(payload.ai_summary.key_changes + payload.ai_summary.priorities),
            )
        )
    return report.title, {
        "title": _truncate(report.title, 256),
        "description": _truncate(report.overview, 2_000),
        "color": 0x5865F2,
        "fields": fields,
    }


def _field(name: str, value: str) -> dict[str, object]:
    return {"name": _truncate(name, 256), "value": _truncate(value, 1_024), "inline": False}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
