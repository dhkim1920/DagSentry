"""Shared operator-facing labels and timestamp formatting."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dagsentry.domain.failure_event import FailureState
from dagsentry.domain.reporting import DailyStatistics


def failure_state_label(state: FailureState) -> str:
    return "최종 실패 (FAILED)" if state == FailureState.FAILED else "재시도 대기 (UP_FOR_RETRY)"


def display_time(value: datetime, timezone: str = "Asia/Seoul") -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")


def failure_list_lines(statistics: DailyStatistics) -> tuple[str, ...]:
    if not statistics.top_failures:
        return (
            "실패 없음"
            if statistics.failure_attempts == 0
            else "상위 실패 목록 미수집 (이관된 리포트)",
        )
    return tuple(
        f"{item.dag_id}.{item.task_id} · 실패 {item.failure_count}회 · "
        f"{item.classification.value if item.classification else '진단 대기'} · "
        f"장애 {item.incident_id or '없음'} "
        f"{item.incident_status.value if item.incident_status else ''} · "
        f"마지막 실패 {display_time(item.last_failed_at, statistics.timezone)}\n"
        f"원인 설명: {item.root_cause or '없음'}"
        for item in statistics.top_failures
    )
