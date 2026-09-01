"""Managed outbound connection values."""

from enum import StrEnum


class ConnectionPurpose(StrEnum):
    """One external dependency selected for each DagSentry environment."""

    AIRFLOW = "AIRFLOW"
    LLM = "LLM"
    NOTIFICATION = "NOTIFICATION"


class ConnectionProvider(StrEnum):
    """Supported implementations for a managed outbound connection."""

    AIRFLOW = "AIRFLOW"
    OLLAMA = "OLLAMA"
    OPENAI = "OPENAI"
    AZURE_OPENAI = "AZURE_OPENAI"
    ANTHROPIC = "ANTHROPIC"
    BEDROCK = "BEDROCK"
    WEBHOOK = "WEBHOOK"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    DISCORD = "DISCORD"


class ConnectionTestStatus(StrEnum):
    """Result of the latest explicit read-only connection test."""

    PASSED = "PASSED"
    FAILED = "FAILED"


class ConnectionTestErrorCategory(StrEnum):
    """Bounded non-secret failure reason for a read-only connection test."""

    CONFIGURATION = "CONFIGURATION"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_RESPONSE = "INVALID_RESPONSE"
