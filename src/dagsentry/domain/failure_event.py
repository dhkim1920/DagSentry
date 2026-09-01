"""Failure Event input contract and deterministic identity."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVENT_KEY_VERSION = 1
Identifier = Annotated[str, Field(min_length=1, max_length=250)]


class CollectionSource(StrEnum):
    """Component that observed a failed Task Try."""

    LISTENER = "LISTENER"
    RETRY_CALLBACK = "RETRY_CALLBACK"
    RECONCILER = "RECONCILER"


class FailureState(StrEnum):
    """Airflow state observed for a failed Task Try."""

    FAILED = "FAILED"
    UP_FOR_RETRY = "UP_FOR_RETRY"


class FailureEventIdentity(BaseModel):
    """Fields that uniquely identify a failed Task Try across Airflow environments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
    dag_id: Identifier
    dag_run_id: Identifier
    task_id: Identifier
    map_index: Annotated[int, Field(ge=-1)]
    try_number: Annotated[int, Field(ge=1)]

    @field_validator("dag_id", "dag_run_id", "task_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value)
        if normalized != normalized.strip():
            raise ValueError("identifier must not have leading or trailing whitespace")
        return normalized


class FailureEventCreate(FailureEventIdentity):
    """Validated request body for ingestion of one failed Task Try."""

    source: CollectionSource
    state: FailureState
    observed_at: datetime
    operator_type: Annotated[str | None, Field(min_length=1, max_length=250)] = None

    @field_validator("observed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_source_state(self) -> Self:
        if (
            self.source is CollectionSource.RETRY_CALLBACK
            and self.state is not FailureState.UP_FOR_RETRY
        ):
            raise ValueError("RETRY_CALLBACK events must have state UP_FOR_RETRY")
        if self.source is CollectionSource.LISTENER and self.state is not FailureState.FAILED:
            raise ValueError("LISTENER events must have state FAILED")
        return self

    def identity(self) -> FailureEventIdentity:
        """Return only the fields participating in Failure Event identity."""
        return FailureEventIdentity(
            environment=self.environment,
            dag_id=self.dag_id,
            dag_run_id=self.dag_run_id,
            task_id=self.task_id,
            map_index=self.map_index,
            try_number=self.try_number,
        )


def canonical_event_identity(identity: FailureEventIdentity) -> bytes:
    """Serialize an identity using the versioned canonical representation."""
    payload = {
        "dag_id": identity.dag_id,
        "dag_run_id": identity.dag_run_id,
        "environment": identity.environment,
        "event_key_version": EVENT_KEY_VERSION,
        "map_index": identity.map_index,
        "task_id": identity.task_id,
        "try_number": identity.try_number,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def make_event_key(identity: FailureEventIdentity) -> str:
    """Return the SHA-256 key for a normalized Failure Event identity."""
    return hashlib.sha256(canonical_event_identity(identity)).hexdigest()
