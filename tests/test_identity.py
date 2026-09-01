from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import (
    BootstrapUnavailableError,
    PasswordPolicyError,
    PasswordService,
    SessionService,
    bootstrap_admin,
    reset_admin_password,
)
from dagsentry.models import AdminAuditEventRecord, UserSessionRecord


def test_password_service_uses_argon2id_and_enforces_minimum_length() -> None:
    service = PasswordService()

    password_hash = service.hash("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert "correct horse battery staple" not in password_hash
    assert service.verify(password_hash, "correct horse battery staple") is True
    assert service.verify(password_hash, "incorrect password") is False
    assert service.needs_rehash(password_hash) is False
    with pytest.raises(PasswordPolicyError):
        service.hash("too-short")


def test_bootstrap_creates_one_normalized_admin_and_safe_audit(session: Session) -> None:
    user = bootstrap_admin(
        session,
        email="  ADMIN@Example.COM ",
        display_name=" Initial Admin ",
        password="correct horse battery staple",
    )

    audit = session.scalar(select(AdminAuditEventRecord))
    assert user.email == "admin@example.com"
    assert user.display_name == "Initial Admin"
    assert user.role == UserRole.ADMIN
    assert user.status == UserStatus.ACTIVE
    assert audit is not None
    assert audit.actor_user_id is None
    assert audit.target_id == user.id
    assert audit.change_summary == {"role": "ADMIN", "source": "bootstrap"}
    assert "correct horse battery staple" not in str(audit.change_summary)
    with pytest.raises(BootstrapUnavailableError):
        bootstrap_admin(
            session,
            email="second@example.com",
            display_name="Second Admin",
            password="another secure password",
        )


def test_session_service_persists_only_hash_and_revokes_token(session: Session) -> None:
    now = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)
    user = bootstrap_admin(
        session,
        email="admin@example.com",
        display_name="Admin",
        password="correct horse battery staple",
        now=now,
    )
    service = SessionService()

    issued = service.issue(session, user.id, now=now)
    record = session.get(UserSessionRecord, issued.session_id)

    assert record is not None
    assert len(issued.token) >= 43
    assert record.token_hash == hashlib.sha256(issued.token.encode("utf-8")).hexdigest()
    assert issued.token not in repr(issued)
    assert issued.expires_at == now + timedelta(hours=12)
    assert service.revoke(session, issued.token, now=now + timedelta(minutes=5)) is True
    session.refresh(record)
    assert record.revoked_at is not None
    assert service.revoke(session, "unknown-token", now=now) is False


def test_reset_admin_password_rehashes_and_revokes_all_sessions(session: Session) -> None:
    now = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)
    passwords = PasswordService()
    user = bootstrap_admin(
        session,
        email="admin@example.com",
        display_name="Admin",
        password="correct horse battery staple",
        password_service=passwords,
        now=now,
    )
    issued = SessionService().issue(session, user.id, now=now)
    previous_hash = user.password_hash

    recovered = reset_admin_password(
        session,
        email=" ADMIN@example.com ",
        password="this is the recovered password",
        password_service=passwords,
        now=now + timedelta(hours=1),
    )

    stored_session = session.get(UserSessionRecord, issued.session_id)
    audits = session.scalars(
        select(AdminAuditEventRecord).order_by(AdminAuditEventRecord.created_at)
    ).all()
    assert recovered.password_hash != previous_hash
    assert passwords.verify(recovered.password_hash, "this is the recovered password")
    assert stored_session is not None and stored_session.revoked_at is not None
    assert audits[-1].action == "user.password_reset"
    assert audits[-1].change_summary == {"source": "recovery_cli", "sessions_revoked": 1}
