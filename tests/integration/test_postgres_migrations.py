from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import quote_plus
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select, text

from dagsentry import __version__
from dagsentry.config import get_settings

pytestmark = pytest.mark.integration

_PREVIOUS_MINOR_REVISION = "0008"
_CURRENT_MINOR_VERSION = "0.3"


def test_empty_schema_upgrade_and_repeated_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    schema = f"migration_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    schema_url = f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    original_url = os.environ.get("DAGSENTRY_DATABASE_URL")
    monkeypatch.setenv("DAGSENTRY_DATABASE_URL", schema_url)
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        command.upgrade(alembic_config, "head")

        schema_engine = create_engine(schema_url)
        assert set(inspect(schema_engine).get_table_names()) >= {
            "alembic_version",
            "failure_events",
            "diagnosis_outbox",
            "error_signatures",
            "diagnoses",
            "incidents",
            "incident_failure_events",
            "incident_state_transitions",
            "incident_recovery_notifications",
            "notification_deliveries",
            "operational_metric_counters",
            "daily_reports",
            "users",
            "user_sessions",
            "admin_audit_events",
            "managed_connections",
        }
        assert "initial_failure_event_id" in {
            column["name"] for column in inspect(schema_engine).get_columns("incidents")
        }
        assert "suppression_reason" in {
            column["name"]
            for column in inspect(schema_engine).get_columns("notification_deliveries")
        }
        assert "csrf_token_hash" in {
            column["name"] for column in inspect(schema_engine).get_columns("user_sessions")
        }
        assert {
            "environment",
            "purpose",
            "provider",
            "non_secret_config",
            "secret_ciphertext",
            "secret_nonce",
            "secret_key_version",
            "version",
        } <= {
            column["name"] for column in inspect(schema_engine).get_columns("managed_connections")
        }
        schema_engine.dispose()
        command.downgrade(alembic_config, "base")
    finally:
        if original_url is None:
            monkeypatch.delenv("DAGSENTRY_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DAGSENTRY_DATABASE_URL", original_url)
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_previous_minor_upgrade_preserves_v02_incident_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    assert __version__.startswith(f"{_CURRENT_MINOR_VERSION}.")

    schema = f"previous_minor_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    schema_url = f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    original_url = os.environ.get("DAGSENTRY_DATABASE_URL")
    monkeypatch.setenv("DAGSENTRY_DATABASE_URL", schema_url)
    get_settings.cache_clear()
    schema_engine = create_engine(schema_url)

    failure_event_id = UUID("10000000-0000-0000-0000-000000000001")
    outbox_id = UUID("10000000-0000-0000-0000-000000000002")
    signature_id = UUID("10000000-0000-0000-0000-000000000003")
    diagnosis_id = UUID("10000000-0000-0000-0000-000000000004")
    delivery_id = UUID("10000000-0000-0000-0000-000000000005")
    incident_id = UUID("10000000-0000-0000-0000-000000000006")
    transition_id = UUID("10000000-0000-0000-0000-000000000007")
    observed_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, _PREVIOUS_MINOR_REVISION)

        legacy = MetaData()
        legacy.reflect(schema_engine)
        with schema_engine.begin() as connection:
            connection.execute(
                legacy.tables["failure_events"].insert(),
                {
                    "id": failure_event_id,
                    "event_key": "e" * 64,
                    "event_key_version": 1,
                    "environment": "production",
                    "dag_id": "legacy_orders",
                    "dag_run_id": "scheduled__2026-08-11T12:00:00+00:00",
                    "task_id": "load_orders",
                    "map_index": -1,
                    "try_number": 1,
                    "source": "LISTENER",
                    "state": "FAILED",
                    "observed_at": observed_at,
                    "operator_type": "SQLExecuteQueryOperator",
                },
            )
            connection.execute(
                legacy.tables["diagnosis_outbox"].insert(),
                {
                    "id": outbox_id,
                    "failure_event_id": failure_event_id,
                    "status": "COMPLETED",
                    "attempt_count": 1,
                    "available_at": observed_at,
                },
            )
            connection.execute(
                legacy.tables["error_signatures"].insert(),
                {
                    "id": signature_id,
                    "fingerprint_version": 1,
                    "fingerprint": "f" * 64,
                    "operator_type": "SQLExecuteQueryOperator",
                    "exception_class": "ConnectionError",
                    "normalized_message": "database host unavailable",
                },
            )
            connection.execute(
                legacy.tables["diagnoses"].insert(),
                {
                    "id": diagnosis_id,
                    "failure_event_id": failure_event_id,
                    "error_signature_id": signature_id,
                    "source": "RULE",
                    "validation_status": "PASSED",
                    "classification": "NETWORK",
                    "root_cause": "database host unavailable",
                    "confidence": 0.95,
                    "matched_rule": "network-connection-error",
                    "extracted_values": {"host": "orders-db"},
                    "evidence": [{"source": "task_log", "excerpt": "connection refused"}],
                    "recommended_actions": ["check database reachability"],
                    "retry_decision": "RETRYABLE",
                    "diagnosis_schema_version": 1,
                    "rule_version": 1,
                    "operator_review_required": False,
                    "validation_errors": [],
                },
            )
            connection.execute(
                legacy.tables["notification_deliveries"].insert(),
                {
                    "id": delivery_id,
                    "diagnosis_id": diagnosis_id,
                    "delivery_key": "d" * 64,
                    "delivery_key_version": 1,
                    "provider": "webhook",
                    "status": "FAILED",
                    "attempt_count": 1,
                    "payload": {"incident_id": str(incident_id)},
                    "last_error_category": "UNAVAILABLE",
                },
            )
            connection.execute(
                legacy.tables["incidents"].insert(),
                {
                    "id": incident_id,
                    "environment": "production",
                    "dag_id": "legacy_orders",
                    "task_id": "load_orders",
                    "error_signature_id": signature_id,
                    "initial_failure_event_id": failure_event_id,
                    "status": "ACKNOWLEDGED",
                },
            )
            connection.execute(
                legacy.tables["incident_failure_events"].insert(),
                {"incident_id": incident_id, "failure_event_id": failure_event_id},
            )
            connection.execute(
                legacy.tables["incident_state_transitions"].insert(),
                {
                    "id": transition_id,
                    "incident_id": incident_id,
                    "previous_status": "OPEN",
                    "status": "ACKNOWLEDGED",
                    "initiator": "OPERATOR",
                    "actor": "legacy-oncall@example.com",
                    "reason": "investigating",
                },
            )

        command.upgrade(alembic_config, "head")

        with schema_engine.begin() as connection:
            connection.execute(
                legacy.tables["notification_deliveries"]
                .update()
                .where(legacy.tables["notification_deliveries"].c.id == delivery_id)
                .values(
                    status="SUPPRESSED",
                    suppression_reason="REPEATED_ACTIVE_INCIDENT",
                )
            )

        with schema_engine.connect() as connection:
            failure = connection.execute(
                select(
                    legacy.tables["failure_events"].c.dag_id,
                    legacy.tables["failure_events"].c.task_id,
                    legacy.tables["failure_events"].c.observed_at,
                ).where(legacy.tables["failure_events"].c.id == failure_event_id)
            ).one()
            assert failure.dag_id == "legacy_orders"
            assert failure.task_id == "load_orders"
            assert failure.observed_at == observed_at

            diagnosis = connection.execute(
                select(
                    legacy.tables["diagnoses"].c.classification,
                    legacy.tables["diagnoses"].c.root_cause,
                    legacy.tables["diagnoses"].c.evidence,
                ).where(legacy.tables["diagnoses"].c.id == diagnosis_id)
            ).one()
            assert diagnosis.classification == "NETWORK"
            assert diagnosis.root_cause == "database host unavailable"
            assert diagnosis.evidence == [{"source": "task_log", "excerpt": "connection refused"}]

            incident = connection.execute(
                select(
                    legacy.tables["incidents"].c.status,
                    legacy.tables["incidents"].c.initial_failure_event_id,
                ).where(legacy.tables["incidents"].c.id == incident_id)
            ).one()
            assert incident.status == "ACKNOWLEDGED"
            assert incident.initial_failure_event_id == failure_event_id
            assert (
                connection.scalar(
                    text("SELECT final_failure_event_id FROM incidents WHERE id = :id"),
                    {"id": incident_id},
                )
                == failure_event_id
            )

            delivery = connection.execute(
                select(
                    legacy.tables["notification_deliveries"].c.status,
                    legacy.tables["notification_deliveries"].c.suppression_reason,
                ).where(legacy.tables["notification_deliveries"].c.id == delivery_id)
            ).one()
            assert delivery.status == "SUPPRESSED"
            assert delivery.suppression_reason == "REPEATED_ACTIVE_INCIDENT"

        assert set(inspect(schema_engine).get_table_names()) >= {
            "incident_recovery_notifications",
            "operational_metric_counters",
            "daily_reports",
            "users",
            "user_sessions",
            "admin_audit_events",
            "managed_connections",
        }
    finally:
        schema_engine.dispose()
        if original_url is None:
            monkeypatch.delenv("DAGSENTRY_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DAGSENTRY_DATABASE_URL", original_url)
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
