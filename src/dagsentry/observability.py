"""Structured logging and request correlation."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime

correlation_id_context: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    """Format the stable fields shared by DagSentry service logs."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "message": record.getMessage(),
        }
        correlation_id = correlation_id_context.get()
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(service: str, level: str) -> None:
    """Configure the process root logger once at service startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def set_correlation_id(correlation_id: str) -> Token[str | None]:
    """Set a correlation ID and return a token used to restore prior context."""
    return correlation_id_context.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore correlation context after request processing."""
    correlation_id_context.reset(token)
