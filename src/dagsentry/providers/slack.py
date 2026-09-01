"""Slack Web API Notification Provider."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from dagsentry.config import Settings
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.outbound_urls import require_credential_endpoint_security

SlackPayload = NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload


@dataclass(frozen=True)
class SlackProviderConfig:
    """Slack chat.postMessage credentials and bounded retry settings."""

    bot_token: str
    channel: str
    base_url: str = "https://slack.com/api"
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5
    trusted_http_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        require_credential_endpoint_security(
            self.base_url,
            provider="Slack API",
            trusted_http_hosts=self.trusted_http_hosts,
        )
        if not self.bot_token:
            raise ValueError("Slack bot token must not be empty")
        if not self.channel:
            raise ValueError("Slack channel must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

    def __repr__(self) -> str:
        return (
            "SlackProviderConfig(bot_token='**********', "
            f"channel={self.channel!r}, base_url={self.base_url!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class SlackNotificationProvider:
    """Send accessible Block Kit messages through Slack chat.postMessage."""

    name = "slack"

    def __init__(
        self,
        config: SlackProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> SlackNotificationProvider:
        """Build the explicitly configured Slack Provider."""
        if settings.notification_provider != "slack":
            raise ValueError("Slack Notification Provider is not enabled")
        if settings.slack_bot_token is None or settings.slack_channel is None:
            raise ValueError("Slack bot token and channel must be configured")
        return cls(
            SlackProviderConfig(
                bot_token=settings.slack_bot_token.get_secret_value(),
                channel=settings.slack_channel,
                base_url=settings.slack_api_base_url,
                timeout_seconds=settings.slack_timeout_seconds,
                max_attempts=settings.slack_max_attempts,
                retry_backoff_seconds=settings.slack_retry_backoff_seconds,
                trusted_http_hosts=settings.insecure_http_trusted_hosts,
            )
        )

    def send(self, payload: SlackPayload, *, delivery_key: str) -> int:
        """Post one message and validate Slack's application-level result."""
        headers = {
            "Authorization": f"Bearer {self.config.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = _message_body(self.config.channel, payload, delivery_key)
        for attempt in range(self.config.max_attempts):
            response: httpx.Response | None = None
            error: NotificationProviderError | None = None
            try:
                response = self.http_client.post(
                    f"{self.config.base_url.rstrip('/')}/chat.postMessage",
                    headers=headers,
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException:
                error = NotificationProviderError(
                    "Slack request timed out",
                    category=NotificationErrorCategory.TIMEOUT,
                    retryable=True,
                )
            except httpx.TransportError:
                error = NotificationProviderError(
                    "Slack is unavailable",
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

        raise RuntimeError("unreachable Slack request state")  # pragma: no cover


def _response_error(response: httpx.Response) -> NotificationProviderError | None:
    status = response.status_code
    if status == 401:
        return _provider_error(NotificationErrorCategory.AUTHENTICATION, False, status)
    if status == 403:
        return _provider_error(NotificationErrorCategory.AUTHORIZATION, False, status)
    if status == 429:
        return _provider_error(NotificationErrorCategory.RATE_LIMITED, True, status)
    if status == 408 or status >= 500:
        return _provider_error(NotificationErrorCategory.UNAVAILABLE, True, status)
    if status >= 400:
        return _provider_error(NotificationErrorCategory.INVALID_REQUEST, False, status)

    try:
        body = response.json()
    except ValueError:
        return _provider_error(NotificationErrorCategory.INVALID_REQUEST, False, status)
    if not isinstance(body, dict) or body.get("ok") is not True:
        code = body.get("error") if isinstance(body, dict) else None
        return _slack_api_error(code if isinstance(code, str) else None, status)
    return None


def _slack_api_error(code: str | None, status: int) -> NotificationProviderError:
    if code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
        return _provider_error(NotificationErrorCategory.AUTHENTICATION, False, status)
    if code in {"missing_scope", "no_permission", "channel_not_found", "is_archived"}:
        return _provider_error(NotificationErrorCategory.AUTHORIZATION, False, status)
    if code == "ratelimited":
        return _provider_error(NotificationErrorCategory.RATE_LIMITED, True, status)
    if code in {"fatal_error", "internal_error", "service_unavailable"}:
        return _provider_error(NotificationErrorCategory.UNAVAILABLE, True, status)
    return _provider_error(NotificationErrorCategory.INVALID_REQUEST, False, status)


def _provider_error(
    category: NotificationErrorCategory,
    retryable: bool,
    response_status: int,
) -> NotificationProviderError:
    return NotificationProviderError(
        f"Slack notification failed: {category.value.lower()}",
        category=category,
        retryable=retryable,
        response_status=response_status,
    )


def _retry_delay(response: httpx.Response | None, config: SlackProviderConfig) -> float:
    if response is None or response.status_code != 429:
        return config.retry_backoff_seconds
    try:
        return min(60.0, max(0.0, float(response.headers["Retry-After"])))
    except (KeyError, ValueError):
        return config.retry_backoff_seconds


def _message_body(channel: str, payload: SlackPayload, delivery_key: str) -> dict[str, object]:
    if isinstance(payload, NotificationPayload):
        text, blocks, event_payload = _diagnosis_message(payload)
        event_type = "dagsentry_diagnosis_created"
    elif isinstance(payload, RecoveryNotificationPayload):
        text, blocks, event_payload = _recovery_message(payload)
        event_type = "dagsentry_incident_recovered"
    else:
        text, blocks, event_payload = _daily_report_message(payload)
        event_type = "dagsentry_daily_report_created"
    event_payload["delivery_key"] = delivery_key
    return {
        "channel": channel,
        "text": _truncate(text, 4_000),
        "blocks": blocks,
        "metadata": {"event_type": event_type, "event_payload": event_payload},
        "unfurl_links": False,
        "unfurl_media": False,
    }


def _diagnosis_message(
    payload: NotificationPayload,
) -> tuple[str, list[dict[str, object]], dict[str, object]]:
    root_cause = payload.root_cause or "No root cause available"
    actions = "; ".join(payload.recommended_actions) or "No recommended actions"
    evidence = "\n".join(f"L{item.line_id}: {item.text}" for item in payload.evidence)
    text = (
        f"DagSentry {payload.classification.value}: {payload.dag_id}.{payload.task_id} failed. "
        f"Root cause: {root_cause}. Retry: {payload.retry_decision.value}. Actions: {actions}"
    )
    details = "\n".join(
        (
            f"*Environment:* {_inline_code(payload.environment)}",
            f"*DAG run:* {_inline_code(payload.dag_run_id)}",
            f"*Task:* {_inline_code(payload.task_id)}  •  "
            f"*Map:* `{payload.map_index}`  •  *Try:* `{payload.try_number}`",
            f"*Failed at:* {_inline_code(payload.failed_at.isoformat())}",
            f"*Incident:* {_inline_code(str(payload.incident_id))}  •  "
            f"*Status:* *{payload.incident_status.value}*  •  "
            f"*Failures:* `{payload.incident_failure_count}`",
            f"*Diagnosis:* *{payload.diagnosis_source.value}*  •  "
            f"*Confidence:* `{payload.confidence:.0%}`  •  "
            f"*Rule fallback:* `{'Yes' if payload.is_rule_fallback else 'No'}`",
            f"*Retry:* *{payload.retry_decision.value}*",
        )
    )
    action_items = (
        "\n".join(f"• {_escape_mrkdwn(action)}" for action in payload.recommended_actions)
        or "• No recommended actions"
    )
    blocks: list[dict[str, object]] = [
        _header_block(f"DagSentry · {payload.classification.value}"),
        _markdown_section_block(
            f"*{_escape_mrkdwn(payload.dag_id)} · {_escape_mrkdwn(payload.task_id)}*\n"
            f"{_code_block(root_cause, 2_800)}"
        ),
        _markdown_section_block(details),
        {"type": "divider"},
        _markdown_section_block(f"*Evidence*\n{_code_block(evidence or 'No evidence', 2_970)}"),
        _markdown_section_block(f"*Recommended actions*\n{action_items}"),
    ]
    if payload.airflow_log_url is not None:
        blocks.append(
            _markdown_section_block(
                f"*Airflow:* <{_escape_mrkdwn(payload.airflow_log_url)}|Open task log>"
            )
        )
    return (
        text,
        blocks,
        {
            "failure_event_id": str(payload.failure_event_id),
            "diagnosis_id": str(payload.diagnosis_id),
            "incident_id": str(payload.incident_id),
            "error_signature": payload.error_signature or "",
        },
    )


def _recovery_message(
    payload: RecoveryNotificationPayload,
) -> tuple[str, list[dict[str, object]], dict[str, object]]:
    text = (
        f"DagSentry recovered: {payload.dag_id}.{payload.task_id} in {payload.environment}; "
        f"{payload.incident_failure_count} linked failure(s)."
    )
    blocks = [
        _header_block("DagSentry · Incident recovered"),
        _section_block(f"{text}\nRecovered at: {payload.recovered_at.isoformat()}"),
    ]
    return text, blocks, {"incident_id": str(payload.incident_id)}


def _daily_report_message(
    payload: DailyReportNotificationPayload,
) -> tuple[str, list[dict[str, object]], dict[str, object]]:
    report = payload.rule_based_report
    text = f"{report.title}. {report.overview} Priorities: {'; '.join(report.priorities)}"
    blocks = [
        _header_block(report.title),
        _section_block(report.overview),
        _section_block("Highlights\n" + "\n".join(report.highlights)),
        _section_block("Priorities\n" + "\n".join(report.priorities)),
    ]
    if payload.ai_summary is not None:
        blocks.append(
            _section_block(
                "AI narrative\n"
                + "\n".join(payload.ai_summary.key_changes + payload.ai_summary.priorities)
            )
        )
    return (
        text,
        blocks,
        {
            "report_date": payload.statistics.report_date.isoformat(),
            "environment": payload.statistics.environment,
        },
    )


def _header_block(text: str) -> dict[str, object]:
    return {"type": "header", "text": {"type": "plain_text", "text": _truncate(text, 150)}}


def _section_block(text: str) -> dict[str, object]:
    return {"type": "section", "text": {"type": "plain_text", "text": _truncate(text, 3_000)}}


def _markdown_section_block(text: str) -> dict[str, object]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text, 3_000)}}


def _escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_code(value: str) -> str:
    escaped = _escape_mrkdwn(value).replace("`", "'")
    return f"`{escaped}`"


def _code_block(value: str, limit: int) -> str:
    escaped = _escape_mrkdwn(value).replace("```", "'''")
    return f"```{_truncate(escaped, limit)}```"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
