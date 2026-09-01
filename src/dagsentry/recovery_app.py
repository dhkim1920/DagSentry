"""One-shot Recovery Checker process entry point."""

from __future__ import annotations

import json
from dataclasses import asdict

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.observability import configure_logging
from dagsentry.recovery import AirflowTaskStateClient, RecoveryChecker
from dagsentry.runtime_connections import ConfiguredNotificationConnectionResolver


def main() -> None:
    """Check active Incidents once for use by an external scheduler."""
    settings = get_settings()
    configure_logging("recovery-checker", settings.log_level)
    session_factory = create_session_factory(settings.database_url)
    notification_provider = (
        ConfiguredNotificationConnectionResolver(
            settings,
            session_factory,
        )
        .resolve(settings.environment)
        .provider
    )
    checker = RecoveryChecker(
        session_factory,
        airflow_client=AirflowTaskStateClient.from_settings(settings),
        notification_provider=notification_provider,
    )
    result = checker.run_once()
    print(json.dumps(asdict(result), sort_keys=True))
    if result.airflow_failures or result.notification_failures:
        raise SystemExit(1)
