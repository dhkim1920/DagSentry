"""Airflow provider metadata used for automatic plugin discovery."""


def get_provider_info() -> dict[str, object]:
    """Return metadata conforming to Airflow's provider info schema."""
    return {
        "package-name": "dagsentry",
        "name": "DagSentry",
        "description": "Airflow failure collection for DagSentry.",
        "plugins": [
            {
                "name": "dagsentry",
                "plugin-class": "dagsentry.airflow.plugin.DagSentryPlugin",
            }
        ],
    }
