"""Airflow listener for final TaskInstance failures."""

from __future__ import annotations

from typing import Any

from airflow.listeners import hookimpl  # type: ignore[import-not-found,unused-ignore]

from dagsentry.airflow.collector import collect_failure, normalized_task_state
from dagsentry.domain.failure_event import CollectionSource, FailureState


class DagSentryListener:
    """Collect final failures while leaving retry failures to the retry callback."""

    @hookimpl  # type: ignore[untyped-decorator,unused-ignore]
    def on_task_instance_failed(
        self,
        previous_state: Any,
        task_instance: Any,
        error: None | str | BaseException,
    ) -> None:
        del previous_state, error
        if normalized_task_state(task_instance) != "FAILED":
            return
        collect_failure(
            task_instance,
            source=CollectionSource.LISTENER,
            state=FailureState.FAILED,
        )


listener = DagSentryListener()
