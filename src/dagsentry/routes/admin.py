"""Admin-only local user lifecycle and audit query routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from dagsentry.db import get_session
from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import IdentityError
from dagsentry.models import AdminAuditEventRecord, UserRecord
from dagsentry.observability import correlation_id_context
from dagsentry.security import AuthenticatedPrincipal, require_admin
from dagsentry.user_management import (
    LastActiveAdminError,
    UserEmailConflictError,
    UserManagementError,
    UserNotFoundError,
    create_user,
    reset_user_password,
    revoke_user_sessions,
    update_user,
)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class UserResponse(BaseModel):
    """Non-secret Admin view of one local user."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: str
    display_name: str
    role: UserRole
    status: UserStatus
    must_change_password: bool
    password_changed_at: datetime
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Stable bounded list of local users."""

    model_config = ConfigDict(extra="forbid")

    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class UserCreateRequest(BaseModel):
    """Admin-created account with a write-only temporary password."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    display_name: str = Field(min_length=1, max_length=250)
    role: UserRole
    temporary_password: str = Field(min_length=1, max_length=1024)


class UserUpdateRequest(BaseModel):
    """Mutable user fields; email remains immutable."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=250)
    role: UserRole | None = None
    status: UserStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> UserUpdateRequest:
        if self.display_name is None and self.role is None and self.status is None:
            raise ValueError("at least one mutable user field is required")
        return self


class PasswordResetRequest(BaseModel):
    """Write-only Admin-issued temporary password."""

    model_config = ConfigDict(extra="forbid")

    temporary_password: str = Field(min_length=1, max_length=1024)


class SessionRevokeResponse(BaseModel):
    """Bounded result of an Admin session revocation operation."""

    model_config = ConfigDict(extra="forbid")

    sessions_revoked: int


class AdminAuditEventResponse(BaseModel):
    """Secret-free administrative audit event."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    actor_user_id: UUID | None
    actor_email: str | None
    action: str
    target_type: str
    target_id: UUID
    change_summary: dict[str, object]
    correlation_id: str | None
    created_at: datetime


class AdminAuditListResponse(BaseModel):
    """Newest-first bounded Admin audit event list."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminAuditEventResponse]
    total: int
    limit: int
    offset: int


@router.get("/users", response_model=UserListResponse)
def list_users(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    """List local users by normalized email."""
    total = session.scalar(select(func.count()).select_from(UserRecord)) or 0
    users = session.scalars(
        select(UserRecord).order_by(UserRecord.email, UserRecord.id).limit(limit).offset(offset)
    ).all()
    return UserListResponse(
        items=[_user_response(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def add_user(
    body: UserCreateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> UserResponse:
    """Create a local account with an Admin-issued temporary password."""
    actor_id = _actor_id(principal)
    try:
        user = create_user(
            session,
            actor_user_id=actor_id,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            temporary_password=body.temporary_password,
            correlation_id=correlation_id_context.get(),
        )
    except UserEmailConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (UserManagementError, IdentityError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _user_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def edit_user(
    user_id: UUID,
    body: UserUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> UserResponse:
    """Update a local user without permitting the last active Admin to disappear."""
    try:
        user = update_user(
            session,
            actor_user_id=_actor_id(principal),
            user_id=user_id,
            display_name=body.display_name,
            role=body.role,
            status=body.status,
            correlation_id=correlation_id_context.get(),
        )
    except UserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except LastActiveAdminError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except UserManagementError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _user_response(user)


@router.post("/users/{user_id}/reset-password", response_model=UserResponse)
def reset_password(
    user_id: UUID,
    body: PasswordResetRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> UserResponse:
    """Set a temporary password and revoke all existing sessions for a user."""
    try:
        user, _ = reset_user_password(
            session,
            actor_user_id=_actor_id(principal),
            user_id=user_id,
            temporary_password=body.temporary_password,
            correlation_id=correlation_id_context.get(),
        )
    except UserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IdentityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _user_response(user)


@router.post("/users/{user_id}/revoke-sessions", response_model=SessionRevokeResponse)
def revoke_sessions(
    user_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> SessionRevokeResponse:
    """Force all current sessions for one user to reauthenticate."""
    try:
        count = revoke_user_sessions(
            session,
            actor_user_id=_actor_id(principal),
            user_id=user_id,
            correlation_id=correlation_id_context.get(),
        )
    except UserNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SessionRevokeResponse(sessions_revoked=count)


@router.get("/audit-events", response_model=AdminAuditListResponse)
def list_audit_events(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminAuditListResponse:
    """List newest Admin audit events with the retained actor email when available."""
    actor = aliased(UserRecord)
    total = session.scalar(select(func.count()).select_from(AdminAuditEventRecord)) or 0
    rows = session.execute(
        select(AdminAuditEventRecord, actor.email)
        .outerjoin(actor, actor.id == AdminAuditEventRecord.actor_user_id)
        .order_by(AdminAuditEventRecord.created_at.desc(), AdminAuditEventRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return AdminAuditListResponse(
        items=[
            AdminAuditEventResponse(
                id=event.id,
                actor_user_id=event.actor_user_id,
                actor_email=actor_email,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                change_summary=event.change_summary,
                correlation_id=event.correlation_id,
                created_at=_as_utc(event.created_at),
            )
            for event, actor_email in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _user_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        must_change_password=user.must_change_password,
        password_changed_at=_as_utc(user.password_changed_at),
        last_login_at=_as_utc(user.last_login_at) if user.last_login_at is not None else None,
        created_at=_as_utc(user.created_at),
        updated_at=_as_utc(user.updated_at),
    )


def _actor_id(principal: AuthenticatedPrincipal) -> UUID:
    if principal.user_id is None:
        raise RuntimeError("Admin principal has no user ID")
    return principal.user_id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
