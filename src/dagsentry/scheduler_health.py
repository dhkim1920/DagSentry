"""Readiness and liveness probe for the dedicated scheduler process."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from dagsentry.config import Settings, get_settings
from dagsentry.db import SessionFactory, create_session_factory
from dagsentry.models import SchedulerHeartbeatRecord
from dagsentry.scheduler import SCHEDULER_NAME


def main() -> None:
    """Exit nonzero unless the persisted scheduler heartbeat is fresh."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-seconds", type=float, default=None)
    arguments = parser.parse_args()
    settings = get_settings()
    healthy, detail = check_scheduler_health(
        settings,
        create_session_factory(settings.database_url),
        max_age_seconds=arguments.max_age_seconds,
    )
    if not healthy:
        raise SystemExit(detail)
    print("scheduler ready")


def check_scheduler_health(
    settings: Settings,
    session_factory: SessionFactory,
    *,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return a probe-safe scheduler heartbeat result without raising for staleness."""
    max_age = max_age_seconds or settings.scheduler_offline_after_seconds
    with session_factory() as session:
        heartbeat = session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME)
    if heartbeat is None:
        return False, "scheduler heartbeat is unavailable"
    age = (_as_utc(now or datetime.now(UTC)) - _as_utc(heartbeat.last_heartbeat_at)).total_seconds()
    if age > max_age:
        return False, f"scheduler heartbeat is stale age_seconds={age:.1f}"
    return True, "scheduler ready"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as error:
        if isinstance(error.code, str):
            print(error.code, file=sys.stderr)
            raise SystemExit(1) from error
        raise
