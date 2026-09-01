"""Transactional Admin operations for local user lifecycle management."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import PasswordService, SessionService, normalize_email
from dagsentry.models import AdminAuditEventRecord, UserRecord

ADMIN_MEMBERSHIP_LOCK_ID = 1_143_776_052


class UserManagementError(ValueError):
    """Base error for an invalid Admin user operation."""


class UserNotFoundError(UserManagementError):
    """No local user matched an Admin operation."""


class UserEmailConflictError(UserManagementError):
    """A normalized local account email is already in use."""


class LastActiveAdminError(UserManagementError):
    """An operation would leave DagSentry without an active Admin."""


def create_user(
    session: Session,
    *,
    actor_user_id: UUID,
    email: str,
    display_name: str,
    role: UserRole,
    temporary_password: str,
    correlation_id: str | None,
    password_service: PasswordService | None = None,
    now: datetime | None = None,
) -> UserRecord:
    """Create an active user who must replace an Admin-issued temporary password."""
    normalized_email = normalize_email(email)
    normalized_display_name = _normalize_display_name(display_name)
    created_at = _as_utc(now or datetime.now(UTC))
    password_hash = (password_service or PasswordService()).hash(temporary_password)
    with session.begin():
        _lock_admin_membership(session)
        if session.scalar(select(UserRecord.id).where(UserRecord.email == normalized_email)):
            raise UserEmailConflictError("email is already in use")
        user = UserRecord(
            email=normalized_email,
            display_name=normalized_display_name,
            role=role,
            status=UserStatus.ACTIVE,
            password_hash=password_hash,
            must_change_password=True,
            password_changed_at=created_at,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(user)
        session.flush()
        _add_audit(
            session,
            actor_user_id=actor_user_id,
            action="user.created",
            target_id=user.id,
            change_summary={"role": role.value, "status": UserStatus.ACTIVE.value},
            correlation_id=correlation_id,
            created_at=created_at,
        )
        session.flush()
    return user


def update_user(
    session: Session,
    *,
    actor_user_id: UUID,
    user_id: UUID,
    display_name: str | None,
    role: UserRole | None,
    status: UserStatus | None,
    correlation_id: str | None,
    now: datetime | None = None,
) -> UserRecord:
    """Update mutable user fields while preserving at least one active Admin."""
    updated_at = _as_utc(now or datetime.now(UTC))
    normalized_display_name = (
        _normalize_display_name(display_name) if display_name is not None else None
    )
    with session.begin():
        _lock_admin_membership(session)
        user = session.scalar(select(UserRecord).where(UserRecord.id == user_id).with_for_update())
        if user is None:
            raise UserNotFoundError("user was not found")
        next_role = role or user.role
        next_status = status or user.status
        removes_active_admin = (
            user.role == UserRole.ADMIN
            and user.status == UserStatus.ACTIVE
            and (next_role != UserRole.ADMIN or next_status != UserStatus.ACTIVE)
        )
        if removes_active_admin and _active_admin_count(session) <= 1:
            raise LastActiveAdminError("the last active Admin cannot be disabled or demoted")

        changes: dict[str, object] = {}
        if normalized_display_name is not None and normalized_display_name != user.display_name:
            changes["display_name"] = {
                "before": user.display_name,
                "after": normalized_display_name,
            }
            user.display_name = normalized_display_name
        if role is not None and role != user.role:
            changes["role"] = {"before": user.role.value, "after": role.value}
            user.role = role
        if status is not None and status != user.status:
            changes["status"] = {"before": user.status.value, "after": status.value}
            user.status = status
        if not changes:
            return user

        user.updated_by_user_id = actor_user_id
        user.updated_at = updated_at
        action = _update_action(changes)
        if user.status == UserStatus.DISABLED:
            SessionService().revoke_all(session, user.id, now=updated_at)
        _add_audit(
            session,
            actor_user_id=actor_user_id,
            action=action,
            target_id=user.id,
            change_summary={"changes": changes},
            correlation_id=correlation_id,
            created_at=updated_at,
        )
        session.flush()
    return user


def reset_user_password(
    session: Session,
    *,
    actor_user_id: UUID,
    user_id: UUID,
    temporary_password: str,
    correlation_id: str | None,
    password_service: PasswordService | None = None,
    now: datetime | None = None,
) -> tuple[UserRecord, int]:
    """Set a temporary password and revoke all sessions for one user."""
    changed_at = _as_utc(now or datetime.now(UTC))
    password_hash = (password_service or PasswordService()).hash(temporary_password)
    with session.begin():
        user = session.scalar(select(UserRecord).where(UserRecord.id == user_id).with_for_update())
        if user is None:
            raise UserNotFoundError("user was not found")
        user.password_hash = password_hash
        user.must_change_password = True
        user.password_changed_at = changed_at
        user.updated_by_user_id = actor_user_id
        user.updated_at = changed_at
        revoked_count = SessionService().revoke_all(session, user.id, now=changed_at)
        _add_audit(
            session,
            actor_user_id=actor_user_id,
            action="user.password_reset",
            target_id=user.id,
            change_summary={"sessions_revoked": revoked_count},
            correlation_id=correlation_id,
            created_at=changed_at,
        )
        session.flush()
    return user, revoked_count


def revoke_user_sessions(
    session: Session,
    *,
    actor_user_id: UUID,
    user_id: UUID,
    correlation_id: str | None,
    now: datetime | None = None,
) -> int:
    """Revoke all active sessions for one existing user and append an audit event."""
    revoked_at = _as_utc(now or datetime.now(UTC))
    with session.begin():
        if session.get(UserRecord, user_id) is None:
            raise UserNotFoundError("user was not found")
        revoked_count = SessionService().revoke_all(session, user_id, now=revoked_at)
        _add_audit(
            session,
            actor_user_id=actor_user_id,
            action="user.sessions_revoked",
            target_id=user_id,
            change_summary={"sessions_revoked": revoked_count},
            correlation_id=correlation_id,
            created_at=revoked_at,
        )
        session.flush()
    return revoked_count


def _lock_admin_membership(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": ADMIN_MEMBERSHIP_LOCK_ID},
        )


def _active_admin_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(UserRecord)
            .where(UserRecord.role == UserRole.ADMIN, UserRecord.status == UserStatus.ACTIVE)
        )
        or 0
    )


def _add_audit(
    session: Session,
    *,
    actor_user_id: UUID,
    action: str,
    target_id: UUID,
    change_summary: dict[str, object],
    correlation_id: str | None,
    created_at: datetime,
) -> None:
    session.add(
        AdminAuditEventRecord(
            actor_user_id=actor_user_id,
            action=action,
            target_type="user",
            target_id=target_id,
            change_summary=change_summary,
            correlation_id=correlation_id,
            created_at=created_at,
        )
    )


def _update_action(changes: dict[str, object]) -> str:
    if "status" in changes:
        status_change = changes["status"]
        if isinstance(status_change, dict) and status_change.get("after") == UserStatus.DISABLED:
            return "user.disabled"
        return "user.enabled"
    if "role" in changes:
        return "user.role_changed"
    return "user.updated"


def _normalize_display_name(display_name: str) -> str:
    normalized = display_name.strip()
    if not normalized or len(normalized) > 250:
        raise UserManagementError("display name must contain between 1 and 250 characters")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
