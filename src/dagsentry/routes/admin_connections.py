"""Admin-only Managed Connection metadata and write-only Secret API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.connection_config import ConnectionConfigError
from dagsentry.connection_crypto import ConnectionEncryptionError, ConnectionSecretCipher
from dagsentry.connection_management import (
    ConnectionConflictError,
    ConnectionManagementError,
    ConnectionNotFoundError,
    disable_connection,
    put_connection,
)
from dagsentry.connection_testing import (
    ConnectionTestOutcome,
    ConnectionTestSnapshot,
    UnsupportedConnectionTestError,
    load_connection_test_snapshot,
    record_connection_test,
    test_connection,
)
from dagsentry.db import get_session
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose, ConnectionTestStatus
from dagsentry.models import ManagedConnectionRecord
from dagsentry.observability import correlation_id_context
from dagsentry.security import AuthenticatedPrincipal, require_admin

router = APIRouter(
    prefix="/api/v1/admin/connections",
    tags=["admin-connections"],
    dependencies=[Depends(require_admin)],
)


class ConnectionPutRequest(BaseModel):
    """Complete non-secret settings plus an optional write-only Secret replacement."""

    model_config = ConfigDict(extra="forbid")

    environment: str = Field(min_length=1, max_length=64)
    purpose: ConnectionPurpose
    provider: ConnectionProvider
    display_name: str = Field(min_length=1, max_length=250)
    non_secret_config: dict[str, object]
    secret: object | None = None
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)


class ConnectionDisableRequest(BaseModel):
    """Optimistic concurrency input for disabling a connection."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class ConnectionResponse(BaseModel):
    """Managed Connection representation that cannot expose encrypted material."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    environment: str
    purpose: ConnectionPurpose
    provider: ConnectionProvider
    display_name: str
    non_secret_config: dict[str, object]
    secret_configured: bool
    enabled: bool
    version: int
    last_tested_at: datetime | None
    last_test_status: ConnectionTestStatus | None
    last_test_error_category: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionListResponse(BaseModel):
    """Stable bounded Admin list of Managed Connections."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConnectionResponse]
    total: int
    limit: int
    offset: int


class ConnectionTestResponse(BaseModel):
    """Bounded read-only result without a Provider response body."""

    model_config = ConfigDict(extra="forbid")

    status: ConnectionTestStatus
    error_category: str | None
    tested_at: datetime


@router.get("", response_model=ConnectionListResponse)
def list_connections(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConnectionListResponse:
    """List Managed Connections without selecting any encrypted columns into the response."""
    total = session.scalar(select(func.count()).select_from(ManagedConnectionRecord)) or 0
    records = session.scalars(
        select(ManagedConnectionRecord)
        .order_by(
            ManagedConnectionRecord.environment,
            ManagedConnectionRecord.purpose,
            ManagedConnectionRecord.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return ConnectionListResponse(
        items=[_connection_response(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(
    connection_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ConnectionResponse:
    """Return one Managed Connection with only a Secret presence indicator."""
    record = session.get(ManagedConnectionRecord, connection_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="connection was not found"
        )
    return _connection_response(record)


@router.put("/{connection_id}", response_model=ConnectionResponse)
def replace_connection(
    connection_id: UUID,
    body: ConnectionPutRequest,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> ConnectionResponse:
    """Create or replace a connection; omitted Secret input preserves existing ciphertext."""
    cipher = _cipher_for_secret(request, body.secret)
    try:
        record = put_connection(
            session,
            connection_id=connection_id,
            actor_user_id=_actor_id(principal),
            environment=body.environment,
            purpose=body.purpose,
            provider=body.provider,
            display_name=body.display_name,
            non_secret_config=body.non_secret_config,
            secret_config=body.secret,
            enabled=body.enabled,
            expected_version=body.expected_version,
            correlation_id=correlation_id_context.get(),
            cipher=cipher,
        )
    except ConnectionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (ConnectionManagementError, ConnectionConfigError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _connection_response(record)


@router.post("/{connection_id}/disable", response_model=ConnectionResponse)
def disable_managed_connection(
    connection_id: UUID,
    body: ConnectionDisableRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> ConnectionResponse:
    """Disable one connection while retaining settings and audit history."""
    try:
        record = disable_connection(
            session,
            connection_id=connection_id,
            actor_user_id=_actor_id(principal),
            expected_version=body.expected_version,
            correlation_id=correlation_id_context.get(),
        )
    except ConnectionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConnectionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _connection_response(record)


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
def test_managed_connection(
    connection_id: UUID,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
) -> ConnectionTestResponse:
    """Run a side-effect-free Provider check and retain only its bounded outcome."""
    try:
        with request.app.state.session_factory() as session:
            snapshot = load_connection_test_snapshot(
                session,
                connection_id=connection_id,
                settings=request.app.state.settings,
            )
        outcome = _run_external_test(request, snapshot)
        with request.app.state.session_factory() as session:
            tested_at = record_connection_test(
                session,
                snapshot=snapshot,
                outcome=outcome,
                actor_user_id=_actor_id(principal),
                correlation_id=correlation_id_context.get(),
            )
    except ConnectionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConnectionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except UnsupportedConnectionTestError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except ConnectionEncryptionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed Connection Secret decryption is unavailable",
        ) from error
    return ConnectionTestResponse(
        status=outcome.status,
        error_category=(
            outcome.error_category.value if outcome.error_category is not None else None
        ),
        tested_at=tested_at,
    )


def _cipher_for_secret(request: Request, secret: object | None) -> ConnectionSecretCipher | None:
    if secret is None:
        return None
    try:
        return ConnectionSecretCipher.from_settings(request.app.state.settings)
    except ConnectionEncryptionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed Connection Secret storage is unavailable",
        ) from error


def _run_external_test(
    request: Request,
    snapshot: ConnectionTestSnapshot,
) -> ConnectionTestOutcome:
    external_client: httpx.Client | None = getattr(
        request.app.state,
        "connection_test_http_client",
        None,
    )
    if external_client is not None:
        return test_connection(snapshot, external_client)
    with httpx.Client(follow_redirects=False) as client:
        return test_connection(snapshot, client)


def _connection_response(record: ManagedConnectionRecord) -> ConnectionResponse:
    return ConnectionResponse(
        id=record.id,
        environment=record.environment,
        purpose=record.purpose,
        provider=record.provider,
        display_name=record.display_name,
        non_secret_config=record.non_secret_config,
        secret_configured=record.secret_ciphertext is not None,
        enabled=record.enabled,
        version=record.version,
        last_tested_at=(
            _as_utc(record.last_tested_at) if record.last_tested_at is not None else None
        ),
        last_test_status=record.last_test_status,
        last_test_error_category=record.last_test_error_category,
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )


def _actor_id(principal: AuthenticatedPrincipal) -> UUID:
    if principal.user_id is None:
        raise RuntimeError("Admin principal has no user ID")
    return principal.user_id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
