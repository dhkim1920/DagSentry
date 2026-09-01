from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

airflow = pytest.importorskip("airflow")
pytest.importorskip("airflow.listeners")

from airflow.providers.standard.operators.empty import (  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
    EmptyOperator,
)

from dagsentry.airflow.listener import DagSentryListener  # noqa: E402
from dagsentry.airflow.plugin import DagSentryPlugin  # noqa: E402
from dagsentry.airflow.policy import install_retry_callback  # noqa: E402
from dagsentry.airflow.provider import get_provider_info  # noqa: E402


@dataclass
class FakeTaskInstance:
    dag_id: str = "daily_orders"
    task_id: str = "load_orders"
    run_id: str = "manual__integration"
    map_index: int = -1
    try_number: int = 1
    end_date: datetime = datetime(2026, 8, 9, tzinfo=UTC)
    operator: str = "PythonOperator"
    state: str = "FAILED"


def test_plugin_registers_listener() -> None:
    assert DagSentryPlugin.name == "dagsentry"
    assert len(DagSentryPlugin.listeners) == 1


def test_provider_metadata_registers_plugin() -> None:
    assert get_provider_info()["plugins"] == [
        {
            "name": "dagsentry",
            "plugin-class": "dagsentry.airflow.plugin.DagSentryPlugin",
        }
    ]


def test_listener_sends_only_final_failed_state() -> None:
    listener = DagSentryListener()
    with patch("dagsentry.airflow.listener.collect_failure") as collect:
        listener.on_task_instance_failed(None, FakeTaskInstance(state="UP_FOR_RETRY"), None)
        collect.assert_not_called()

        task_instance = FakeTaskInstance(state="FAILED")
        listener.on_task_instance_failed(None, task_instance, RuntimeError("failed"))
        collect.assert_called_once()


def test_policy_composes_with_airflow_operator_callback() -> None:
    def existing_callback(_: object) -> None:
        pass

    task = EmptyOperator(task_id="compatibility_test", on_retry_callback=existing_callback)

    install_retry_callback(task)

    assert isinstance(task.on_retry_callback, list)
    assert task.on_retry_callback[0] is existing_callback


def test_airflow_openapi_exposes_exact_task_try_log_contract() -> None:
    assert airflow.__file__ is not None
    specification = (
        Path(airflow.__file__).parent / "api_fastapi/core_api/openapi/v2-rest-api-generated.yaml"
    ).read_text()
    endpoint = (
        "/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/logs/{try_number}:"
    )
    endpoint_start = specification.index(endpoint)
    endpoint_end = specification.index("\n  /api/v2/", endpoint_start + len(endpoint))
    endpoint_contract = specification[endpoint_start:endpoint_end]

    assert "operationId: get_log" in endpoint_contract
    assert "- name: map_index" in endpoint_contract
    assert "- name: token" in endpoint_contract
    assert "$ref: '#/components/schemas/TaskInstancesLogResponse'" in endpoint_contract


def test_airflow_openapi_exposes_reconciler_task_history_contract() -> None:
    assert airflow.__file__ is not None
    contract = (
        Path(airflow.__file__).parent / "api_fastapi/core_api/openapi/v2-rest-api-generated.yaml"
    ).read_text()

    collection_start = contract.index("/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances:")
    collection_end = contract.index("\n  /api/v2/", collection_start + 1)
    collection_contract = contract[collection_start:collection_end]
    assert "updated_at_gte" in collection_contract
    assert "updated_at_lt" in collection_contract
    assert "- name: task_id" in collection_contract
    assert "- name: map_index" in collection_contract
    assert "next_cursor" in collection_contract
    assert "$ref: '#/components/schemas/TaskInstanceCollectionResponse'" in collection_contract

    tries_path = "/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/tries:"
    tries_start = contract.index(tries_path)
    tries_end = contract.index("\n  /api/v2/", tries_start + 1)
    tries_contract = contract[tries_start:tries_end]
    assert "map_index" in tries_contract
    assert "$ref: '#/components/schemas/TaskInstanceHistoryCollectionResponse'" in tries_contract
