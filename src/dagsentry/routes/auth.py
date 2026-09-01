"""Local user login, session, and password routes."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.config import Settings
from dagsentry.db import get_session
from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import (
    CurrentPasswordError,
    IdentityError,
    PasswordService,
    SessionService,
    change_user_password,
    normalize_email,
)
from dagsentry.metrics import record_counter
from dagsentry.models import UserRecord
from dagsentry.observability import correlation_id_context
from dagsentry.security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthenticatedPrincipal,
    require_user_session,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
PASSWORD_SERVICE = PasswordService()
DUMMY_PASSWORD_HASH = PASSWORD_SERVICE.hash("DagSentry timing protection value")


class _LoginRejected(Exception):
    pass


class LoginRateLimiter:
    """Bound failed login work per client without retaining account identifiers."""

    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow_attempt(self, client: str) -> bool:
        with self._lock:
            failures = self._recent_failures(client)
            if len(failures) >= self.attempts:
                return False
            failures.append(monotonic())
            return True

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _recent_failures(self, client: str) -> deque[float]:
        now = monotonic()
        failures = self._failures.setdefault(client, deque())
        while failures and now - failures[0] >= self.window_seconds:
            failures.popleft()
        if not failures:
            self._failures.pop(client, None)
            failures = self._failures.setdefault(client, deque())
        return failures


class LoginRequest(BaseModel):
    """Local account credentials accepted only in the request body."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class AuthUserResponse(BaseModel):
    """Non-secret identity returned for a browser session."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: str
    display_name: str
    role: UserRole
    must_change_password: bool


class LoginResponse(BaseModel):
    """Login result; the session secret itself is set only as an HttpOnly cookie."""

    model_config = ConfigDict(extra="forbid")

    user: AuthUserResponse


class ChangePasswordRequest(BaseModel):
    """Current and replacement passwords for the authenticated account."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> LoginResponse:
    """Verify a local account and issue fixed-expiry browser cookies."""
    client = request.client.host if request.client is not None else "unknown"
    limiter: LoginRateLimiter = request.app.state.login_rate_limiter
    if not limiter.allow_attempt(client):
        _record_auth_event(request, "login_rate_limited")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts; try again later",
        )
    try:
        normalized_email = normalize_email(body.email)
    except IdentityError:
        normalized_email = ""
    try:
        with session.begin():
            user = session.scalar(select(UserRecord).where(UserRecord.email == normalized_email))
            candidate_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            password_matches = PASSWORD_SERVICE.verify(candidate_hash, body.password)
            if user is None or user.status != UserStatus.ACTIVE or not password_matches:
                raise _LoginRejected
            now = datetime.now(UTC)
            if PASSWORD_SERVICE.needs_rehash(user.password_hash):
                user.password_hash = PASSWORD_SERVICE.hash(body.password)
                user.password_changed_at = now
            user.last_login_at = now
            user.updated_at = now
            issued = SessionService(
                timedelta(hours=request.app.state.settings.session_ttl_hours)
            ).issue(
                session,
                user.id,
                now=now,
            )
    except _LoginRejected as error:
        _record_auth_event(request, "login_failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from error

    secure = _secure_cookie(request)
    limiter.reset(client)
    max_age = request.app.state.settings.session_ttl_hours * 60 * 60
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        max_age=max_age,
        secure=secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        issued.csrf_token,
        max_age=max_age,
        secure=secure,
        httponly=False,
        samesite="strict",
        path="/",
    )
    _record_auth_event(request, "login_success")
    return LoginResponse(user=_user_response(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user_session)],
) -> Response:
    """Revoke the current user session and expire its browser cookies."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is not None:
        with request.app.state.session_factory() as session:
            SessionService().revoke(session, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _delete_auth_cookies(response, secure=_secure_cookie(request))
    _record_auth_event(request, "logout")
    return response


@router.get("/me", response_model=AuthUserResponse)
def me(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user_session)],
) -> AuthUserResponse:
    """Return the user represented by the current active session."""
    if principal.user_id is None or principal.identity is None or principal.display_name is None:
        raise RuntimeError("user session principal is incomplete")
    return AuthUserResponse(
        id=principal.user_id,
        email=principal.identity,
        display_name=principal.display_name,
        role=principal.role,
        must_change_password=principal.must_change_password,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user_session)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Replace the current password and revoke every session for the user."""
    if principal.user_id is None:
        raise RuntimeError("user session principal has no user ID")
    try:
        change_user_password(
            session,
            user_id=principal.user_id,
            current_password=body.current_password,
            new_password=body.new_password,
            correlation_id=correlation_id_context.get(),
            password_service=PASSWORD_SERVICE,
        )
    except CurrentPasswordError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        ) from error
    except IdentityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _delete_auth_cookies(response, secure=_secure_cookie(request))
    _record_auth_event(request, "password_changed")
    return response


def _user_response(user: UserRecord) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
    )


def _secure_cookie(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.environment == "production"


def _delete_auth_cookies(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=False,
        samesite="strict",
    )


def _record_auth_event(request: Request, event: str) -> None:
    record_counter(
        request.app.state.session_factory,
        "dagsentry_auth_events_total",
        event,
    )
