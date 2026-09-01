"""Dedicated APScheduler process entry point."""

from __future__ import annotations

import signal

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.observability import configure_logging
from dagsentry.scheduler import DailyReportScheduler


def main() -> None:
    """Run the Daily Report scheduler until SIGINT or SIGTERM."""
    settings = get_settings()
    configure_logging("scheduler", settings.log_level)
    scheduler = DailyReportScheduler(settings, create_session_factory(settings.database_url))

    def stop(*_: object) -> None:
        scheduler.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    scheduler.run_forever()
