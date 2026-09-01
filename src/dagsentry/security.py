"""Authentication used by DagSentry HTTP APIs."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select

from dagsentry.config import Settings
from dagsentry.domain.identity import UserRole
from dagsentry.identity import SessionAuthenticationError, SessionCsrfError, SessionService
from dagsentry.models import UserRecord

SESSION_COOKIE_NAME = "dagsentry_session"
CSRF_COOKIE_NAME = "dagsentry_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PASSWORD_CHANGE_ALLOWED_PATHS = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/auth/change-password",
    }
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Trusted identity derived only from a server-side credential or session."""

    role: UserRole
    identity: str | None
    user_id: UUID | None = None
    display_name: str | None = None
    must_change_password: bool = False

    @property
    def is_user_session(self) -> bool:
        return self.user_id is not None


def verify_ingest_token(
    request: Request,
    provided_token: Annotated[str | None, Header(alias="X-DagSentry-Token")] = None,
) -> None:
    """Verify the shared token configured for Airflow collectors."""
    settings: Settings = request.app.state.settings
    configured_token = settings.ingest_api_token
    if configured_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failure ingestion is not configured",
        )
    if provided_token is None or not secrets.compare_digest(
        provided_token, configured_token.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ingest token",
        )


def authenticate_query_principal(
    request: Request,
    viewer_token: Annotated[str | None, Header(alias="X-DagSentry-Viewer-Token")] = None,
    operator_token: Annotated[str | None, Header(alias="X-DagSentry-Operator-Token")] = None,
) -> AuthenticatedPrincipal:
    """Authenticate a local user session or a legacy shared query credential."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token is not None and (viewer_token is not None or operator_token is not None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple authentication methods are not allowed",
        )
    if session_token is not None:
        csrf_token = request.headers.get(CSRF_HEADER_NAME)
        try:
            with request.app.state.session_factory() as database_session:
                identity = SessionService().authenticate(
                    database_session,
                    session_token,
                    csrf_token=csrf_token,
                    require_csrf=request.method not in SAFE_HTTP_METHODS,
                )
        except SessionCsrfError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid CSRF token",
            ) from error
        except SessionAuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
            ) from error
        if identity.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Password change required",
            )
        return AuthenticatedPrincipal(
            role=identity.role,
            identity=identity.email,
            user_id=identity.user_id,
            display_name=identity.display_name,
            must_change_password=identity.must_change_password,
        )

    settings: Settings = request.app.state.settings
    viewer_configured = settings.viewer_api_token
    operator_configured = settings.operator_api_token
    operator_identity = settings.operator_api_identity
    if operator_token is not None:
        if operator_configured is None or not secrets.compare_digest(
            operator_token,
            operator_configured.get_secret_value(),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token",
            )
        if operator_identity is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Operator identity is not configured",
            )
        return AuthenticatedPrincipal(UserRole.OPERATOR, operator_identity)

    if (
        viewer_token is not None
        and viewer_configured is not None
        and secrets.compare_digest(
            viewer_token,
            viewer_configured.get_secret_value(),
        )
    ):
        return AuthenticatedPrincipal(UserRole.VIEWER, None)

    if viewer_configured is None and operator_configured is None:
        with request.app.state.session_factory() as database_session:
            has_local_user = database_session.scalar(select(UserRecord.id).limit(1)) is not None
        if not has_local_user:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Query APIs are not configured",
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid access token",
    )


def require_operator(
    principal: Annotated[AuthenticatedPrincipal, Depends(authenticate_query_principal)],
) -> AuthenticatedPrincipal:
    """Reject authenticated Viewers from state-changing operations."""
    if principal.role not in {UserRole.OPERATOR, UserRole.ADMIN}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator role required",
        )
    return principal


def require_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(authenticate_query_principal)],
) -> AuthenticatedPrincipal:
    """Reject non-Admin principals from administrative operations."""
    if principal.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return principal


def require_user_session(
    principal: Annotated[AuthenticatedPrincipal, Depends(authenticate_query_principal)],
) -> AuthenticatedPrincipal:
    """Reject legacy shared credentials from user account operations."""
    if not principal.is_user_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User session required",
        )
    return principal
