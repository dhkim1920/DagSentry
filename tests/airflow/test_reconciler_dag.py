from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.dag_processing.dagbag import DagBag  # noqa: E402


def test_reconciler_dag_loads_with_single_active_run_and_task_retries() -> None:
    dag_folder = Path(__file__).parents[2] / "dags"
    dag_bag = DagBag(dag_folder=str(dag_folder))

    assert dag_bag.import_errors == {}
    dag = dag_bag.dags["dagsentry_failure_reconciler"]
    assert dag.max_active_runs == 1
    assert dag.get_task("reconcile").retries == 2
