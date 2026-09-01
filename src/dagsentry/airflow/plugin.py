"""Airflow plugin registration for DagSentry listeners."""

from airflow.plugins_manager import AirflowPlugin  # type: ignore[import-not-found,unused-ignore]

from dagsentry.airflow.listener import listener


class DagSentryPlugin(AirflowPlugin):
    """Register DagSentry's process-wide Airflow listeners."""

    name = "dagsentry"
    listeners = [listener]
