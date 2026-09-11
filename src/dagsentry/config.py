"""Environment-backed DagSentry configuration."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by DagSentry services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DAGSENTRY_",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://dagsentry:dagsentry@localhost:5432/dagsentry"
    environment: str = Field(default="production", pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    ingest_api_token: SecretStr | None = None
    viewer_api_token: SecretStr | None = None
    operator_api_token: SecretStr | None = None
    operator_api_identity: str | None = Field(default=None, min_length=1, max_length=250)
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    login_rate_limit_attempts: int = Field(default=10, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    connection_encryption_key: SecretStr | None = None
    connection_encryption_key_version: int = Field(default=1, ge=1)
    ingest_max_request_bytes: int = Field(default=65_536, ge=1)
    airflow_config_source: Literal["environment", "database"] = "environment"
    airflow_api_base_url: str | None = None
    airflow_api_token: SecretStr | None = None
    airflow_api_timeout_seconds: float = Field(default=5.0, gt=0)
    airflow_api_max_attempts: int = Field(default=2, ge=1)
    airflow_api_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    airflow_log_max_response_bytes: int = Field(default=1_048_576, ge=1)
    llm_config_source: Literal["environment", "database"] = "environment"
    llm_provider: Literal["openai", "azure_openai", "anthropic", "bedrock", "ollama"] | None = None
    llm_model: str | None = None
    llm_prompt_version: str = "ai-diagnosis-ko-v1"
    daily_report_prompt_version: str = "daily-report-ko-v1"
    report_timezone: str = "Asia/Seoul"
    llm_timeout_seconds: float = Field(default=15.0, gt=0)
    llm_max_attempts: int = Field(default=2, ge=1)
    llm_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    openai_api_base_url: str = "https://api.openai.com/v1"
    insecure_http_trusted_hosts: frozenset[str] = Field(default_factory=frozenset)
    openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    anthropic_api_base_url: str = "https://api.anthropic.com/v1"
    anthropic_api_key: SecretStr | None = None
    anthropic_max_output_tokens: int = Field(default=2_048, ge=1)
    bedrock_region: str | None = None
    bedrock_max_output_tokens: int = Field(default=2_048, ge=1)
    ollama_api_base_url: str = "http://localhost:11434/api"
    ollama_max_output_tokens: int = Field(default=2_048, ge=1)
    notification_config_source: Literal["environment", "database"] = "environment"
    notification_provider: Literal["webhook", "slack", "teams", "discord", "smtp"] = "webhook"
    notification_fallback_providers: list[
        Literal["webhook", "slack", "teams", "discord", "smtp"]
    ] = Field(default_factory=list)
    webhook_url: str | None = None
    webhook_bearer_token: SecretStr | None = None
    webhook_timeout_seconds: float = Field(default=5.0, gt=0)
    webhook_max_attempts: int = Field(default=2, ge=1)
    webhook_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    slack_bot_token: SecretStr | None = None
    slack_channel: str | None = None
    slack_api_base_url: str = "https://slack.com/api"
    slack_timeout_seconds: float = Field(default=5.0, gt=0)
    slack_max_attempts: int = Field(default=2, ge=1)
    slack_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    teams_webhook_url: str | None = None
    teams_timeout_seconds: float = Field(default=5.0, gt=0)
    teams_max_attempts: int = Field(default=2, ge=1)
    teams_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    discord_webhook_url: str | None = None
    discord_timeout_seconds: float = Field(default=5.0, gt=0)
    discord_max_attempts: int = Field(default=2, ge=1)
    discord_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_to: list[str] = Field(default_factory=list)
    smtp_use_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=5.0, gt=0)
    smtp_max_attempts: int = Field(default=2, ge=1)
    smtp_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    airflow_ui_base_url: str | None = None
    display_timezone: str = "Asia/Seoul"

    @field_validator("display_timezone", "report_timezone")
    @classmethod
    def validate_display_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("display_timezone must be an IANA timezone") from error
        return value

    diagnosis_reuse_max_age_days: int = Field(default=30, ge=1)
    worker_max_attempts: int = Field(default=5, ge=1)
    worker_backoff_base_seconds: float = Field(default=5.0, ge=0)
    worker_backoff_max_seconds: float = Field(default=300.0, ge=0)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    worker_stale_lock_timeout_seconds: float = Field(default=900.0, gt=0)
    scheduler_poll_interval_seconds: float = Field(default=5.0, gt=0)
    scheduler_heartbeat_interval_seconds: float = Field(default=10.0, gt=0)
    scheduler_offline_after_seconds: float = Field(default=30.0, gt=0)
    log_max_input_chars: int = Field(default=1_048_576, ge=1)
    log_excerpt_max_lines: int = Field(default=80, ge=1)
    log_excerpt_max_chars: int = Field(default=16_000, ge=1)
    log_excerpt_context_lines: int = Field(default=2, ge=0)
    log_secret_patterns: list[str] = Field(default_factory=list)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load and cache settings for one process."""
    return Settings()
