"""Diagnosis Worker process entry point."""

from __future__ import annotations

import os
import socket
from datetime import timedelta

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.domain.diagnosis import (
    DIAGNOSIS_SCHEMA_VERSION,
    DiagnosisReusePolicy,
    DiagnosisVersions,
)
from dagsentry.error_signature import FINGERPRINT_VERSION
from dagsentry.log_processing import LogProcessingConfig, LogProcessor
from dagsentry.observability import configure_logging
from dagsentry.pipeline import DiagnosisPipeline
from dagsentry.rule_diagnosis import RULESET_VERSION, RuleEngine
from dagsentry.runtime_connections import (
    ConfiguredAirflowConnectionResolver,
    ConfiguredLLMConnectionResolver,
    ConfiguredNotificationConnectionResolver,
)
from dagsentry.worker import DiagnosisWorker, WorkerOptions


def main() -> None:
    """Build and run one production Worker process."""
    settings = get_settings()
    configure_logging("worker", settings.log_level)
    session_factory = create_session_factory(settings.database_url)
    pipeline = DiagnosisPipeline(
        session_factory,
        airflow_connection_resolver=ConfiguredAirflowConnectionResolver(
            settings,
            session_factory,
        ),
        log_processor=LogProcessor(LogProcessingConfig.from_settings(settings)),
        rule_engine=RuleEngine(),
        reuse_policy=DiagnosisReusePolicy(
            fingerprint_version=FINGERPRINT_VERSION,
            versions=DiagnosisVersions(
                schema_version=DIAGNOSIS_SCHEMA_VERSION,
                prompt_version=settings.llm_prompt_version,
                rule_version=RULESET_VERSION,
            ),
            max_age=timedelta(days=settings.diagnosis_reuse_max_age_days),
        ),
        notification_connection_resolver=ConfiguredNotificationConnectionResolver(
            settings,
            session_factory,
        ),
        llm_connection_resolver=ConfiguredLLMConnectionResolver(
            settings,
            session_factory,
        ),
    )
    worker = DiagnosisWorker(
        session_factory,
        pipeline.handle,
        f"{socket.gethostname()}:{os.getpid()}",
        options=WorkerOptions(
            max_attempts=settings.worker_max_attempts,
            backoff_base_seconds=settings.worker_backoff_base_seconds,
            backoff_max_seconds=settings.worker_backoff_max_seconds,
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            stale_lock_timeout_seconds=settings.worker_stale_lock_timeout_seconds,
        ),
    )
    worker.install_signal_handlers()
    worker.run()
