"""Local account password, session, and bootstrap operations."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.models import AdminAuditEventRecord, UserRecord, UserSessionRecord

MINIMUM_PASSWORD_LENGTH = 12
DEFAULT_SESSION_TTL = timedelta(hours=12)


class IdentityError(ValueError):
    """Base error for an invalid identity operation."""


class PasswordPolicyError(IdentityError):
    """A new password does not meet the local password policy."""


class BootstrapUnavailableError(IdentityError):
    """The one-time initial Admin bootstrap is no longer available."""


class AdminNotFoundError(IdentityError):
    """No Admin account matched a recovery request."""


class SessionUserUnavailableError(IdentityError):
    """A session cannot be issued for the requested user."""


class SessionAuthenticationError(IdentityError):
    """An opaque session is missing, expired, revoked, or owned by a disabled user."""


class SessionCsrfError(IdentityError):
    """A state-changing session request did not prove its CSRF secret."""


class CurrentPasswordError(IdentityError):
    """The supplied current password does not match the account."""


class PasswordService:
    """Hash and verify passwords using one versioned Argon2id policy."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def hash(self, password: str) -> str:
        """Validate and hash a new password."""
        if len(password) < MINIMUM_PASSWORD_LENGTH:
            raise PasswordPolicyError(
                f"password must contain at least {MINIMUM_PASSWORD_LENGTH} characters"
            )
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Return whether a password matches without exposing verification errors."""
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """Return whether a valid hash uses an older password policy."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


@dataclass(frozen=True)
class IssuedSession:
    """One raw session token returned only at issuance time."""

    session_id: UUID
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class SessionIdentity:
    """Non-secret user identity resolved from one active session."""

    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    must_change_password: bool


class SessionService:
    """Issue and revoke opaque tokens while persisting only their SHA-256 digest."""

    def __init__(self, ttl: timedelta = DEFAULT_SESSION_TTL) -> None:
        if ttl <= timedelta(0):
            raise ValueError("session ttl must be positive")
        self._ttl = ttl

    def issue(
        self,
        session: Session,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IssuedSession:
        """Create a fixed-expiry session for one active user."""
        issued_at = _as_utc(now or datetime.now(UTC))
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with _transaction(session):
            user = session.get(UserRecord, user_id)
            if user is None or user.status != UserStatus.ACTIVE:
                raise SessionUserUnavailableError("session user is not active")
            record = UserSessionRecord(
                user_id=user_id,
                token_hash=_token_hash(token),
                csrf_token_hash=_token_hash(csrf_token),
                expires_at=issued_at + self._ttl,
                created_at=issued_at,
                last_seen_at=issued_at,
            )
            session.add(record)
            session.flush()
            result = IssuedSession(record.id, token, csrf_token, record.expires_at)
        return result

    def authenticate(
        self,
        session: Session,
        token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
        now: datetime | None = None,
    ) -> SessionIdentity:
        """Resolve one active session and optionally verify its bound CSRF token."""
        checked_at = _as_utc(now or datetime.now(UTC))
        row = session.execute(
            select(UserSessionRecord, UserRecord)
            .join(UserRecord, UserRecord.id == UserSessionRecord.user_id)
            .where(UserSessionRecord.token_hash == _token_hash(token))
        ).one_or_none()
        if row is None:
            raise SessionAuthenticationError("session is not active")
        record, user = row
        if (
            record.revoked_at is not None
            or _as_utc(record.expires_at) <= checked_at
            or user.status != UserStatus.ACTIVE
        ):
            raise SessionAuthenticationError("session is not active")
        if require_csrf and (
            csrf_token is None
            or not secrets.compare_digest(record.csrf_token_hash, _token_hash(csrf_token))
        ):
            raise SessionCsrfError("CSRF token is invalid")
        return SessionIdentity(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            must_change_password=user.must_change_password,
        )

    def revoke(
        self,
        session: Session,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Revoke a token if it exists, without revealing lookup details to callers."""
        revoked_at = _as_utc(now or datetime.now(UTC))
        with _transaction(session):
            record = session.scalar(
                select(UserSessionRecord).where(UserSessionRecord.token_hash == _token_hash(token))
            )
            if record is None:
                return False
            if record.revoked_at is None:
                record.revoked_at = revoked_at
                session.flush()
            return True

    def revoke_all(
        self,
        session: Session,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> int:
        """Revoke every active session for one user in the current transaction."""
        revoked_at = _as_utc(now or datetime.now(UTC))
        with _transaction(session):
            active_sessions = session.scalars(
                select(UserSessionRecord).where(
                    UserSessionRecord.user_id == user_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
            ).all()
            for active_session in active_sessions:
                active_session.revoked_at = revoked_at
            session.flush()
            return len(active_sessions)


def bootstrap_admin(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    password_service: PasswordService | None = None,
    now: datetime | None = None,
) -> UserRecord:
    """Create the first and only bootstrap account in an empty user store."""
    normalized_email = normalize_email(email)
    normalized_display_name = _normalize_display_name(display_name)
    changed_at = _as_utc(now or datetime.now(UTC))
    password_hash = (password_service or PasswordService()).hash(password)
    with _transaction(session):
        if session.scalar(select(UserRecord.id).limit(1)) is not None:
            raise BootstrapUnavailableError("initial Admin has already been bootstrapped")
        user = UserRecord(
            email=normalized_email,
            display_name=normalized_display_name,
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            password_hash=password_hash,
            must_change_password=False,
            password_changed_at=changed_at,
            created_at=changed_at,
            updated_at=changed_at,
        )
        session.add(user)
        session.flush()
        session.add(
            AdminAuditEventRecord(
                actor_user_id=None,
                action="user.created",
                target_type="user",
                target_id=user.id,
                change_summary={"role": UserRole.ADMIN.value, "source": "bootstrap"},
                correlation_id=None,
                created_at=changed_at,
            )
        )
        session.flush()
    return user


def reset_admin_password(
    session: Session,
    *,
    email: str,
    password: str,
    password_service: PasswordService | None = None,
    now: datetime | None = None,
) -> UserRecord:
    """Recover one Admin password and revoke all of that account's sessions."""
    normalized_email = normalize_email(email)
    changed_at = _as_utc(now or datetime.now(UTC))
    password_hash = (password_service or PasswordService()).hash(password)
    with _transaction(session):
        user = session.scalar(
            select(UserRecord)
            .where(UserRecord.email == normalized_email, UserRecord.role == UserRole.ADMIN)
            .with_for_update()
        )
        if user is None:
            raise AdminNotFoundError("Admin account was not found")
        user.password_hash = password_hash
        user.password_changed_at = changed_at
        user.must_change_password = False
        user.updated_at = changed_at
        active_sessions = session.scalars(
            select(UserSessionRecord).where(
                UserSessionRecord.user_id == user.id,
                UserSessionRecord.revoked_at.is_(None),
            )
        ).all()
        for active_session in active_sessions:
            active_session.revoked_at = changed_at
        session.add(
            AdminAuditEventRecord(
                actor_user_id=None,
                action="user.password_reset",
                target_type="user",
                target_id=user.id,
                change_summary={
                    "source": "recovery_cli",
                    "sessions_revoked": len(active_sessions),
                },
                correlation_id=None,
                created_at=changed_at,
            )
        )
        session.flush()
    return user


def change_user_password(
    session: Session,
    *,
    user_id: UUID,
    current_password: str,
    new_password: str,
    correlation_id: str | None,
    password_service: PasswordService | None = None,
    session_service: SessionService | None = None,
    now: datetime | None = None,
) -> UserRecord:
    """Change a user's password, revoke all sessions, and append an audit event."""
    changed_at = _as_utc(now or datetime.now(UTC))
    passwords = password_service or PasswordService()
    sessions = session_service or SessionService()
    with _transaction(session):
        user = session.scalar(select(UserRecord).where(UserRecord.id == user_id).with_for_update())
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or not passwords.verify(user.password_hash, current_password)
        ):
            raise CurrentPasswordError("current password is incorrect")
        user.password_hash = passwords.hash(new_password)
        user.password_changed_at = changed_at
        user.must_change_password = False
        user.updated_at = changed_at
        revoked_count = sessions.revoke_all(session, user.id, now=changed_at)
        session.add(
            AdminAuditEventRecord(
                actor_user_id=user.id,
                action="user.password_changed",
                target_type="user",
                target_id=user.id,
                change_summary={"sessions_revoked": revoked_count},
                correlation_id=correlation_id,
                created_at=changed_at,
            )
        )
        session.flush()
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    if session.in_transaction():
        yield
        return
    with session.begin():
        yield


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or len(normalized) > 320 or "@" not in normalized:
        raise IdentityError("email must be a valid address with at most 320 characters")
    return normalized


def _normalize_display_name(display_name: str) -> str:
    normalized = display_name.strip()
    if not normalized or len(normalized) > 250:
        raise IdentityError("display name must contain between 1 and 250 characters")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
