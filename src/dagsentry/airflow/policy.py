"""Cluster-policy helper that installs retry collection on every task."""

from __future__ import annotations

from typing import Any

from dagsentry.airflow.collector import collect_retry_failure


def install_retry_callback(task: Any) -> None:
    """Append DagSentry's callback while preserving user callbacks and their order."""
    current = getattr(task, "on_retry_callback", None)
    if current is None:
        task.on_retry_callback = collect_retry_failure
        return
    if current is collect_retry_failure:
        return
    if isinstance(current, list):
        if any(callback is collect_retry_failure for callback in current):
            return
        task.on_retry_callback = [*current, collect_retry_failure]
        return
    task.on_retry_callback = [current, collect_retry_failure]
