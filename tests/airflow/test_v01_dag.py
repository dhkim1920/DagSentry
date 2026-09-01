from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.dag_processing.dagbag import DagBag  # noqa: E402


def test_v01_failure_scenario_dag_loads_with_retry_and_mapping() -> None:
    dag_folder = Path(__file__).parent / "dags"
    dag_bag = DagBag(dag_folder=str(dag_folder))

    assert dag_bag.import_errors == {}
    dag = dag_bag.dags["dagsentry_v01_failure_scenarios"]
    assert dag.get_task("retry_then_success").retries == 1
    assert dag.get_task("always_fails").retries == 2
    assert dag.get_task("mapped_failure").is_mapped is True
