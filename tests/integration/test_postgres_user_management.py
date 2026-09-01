from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import quote_plus
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.domain.identity import UserRole, UserStatus
from dagsentry.identity import bootstrap_admin
from dagsentry.models import UserRecord
from dagsentry.user_management import LastActiveAdminError, create_user, update_user

pytestmark = pytest.mark.integration


def test_concurrent_admin_demotions_preserve_one_active_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    schema = f"admin_concurrency_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in database_url else "?"
    schema_url = f"{database_url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    original_url = os.environ.get("DAGSENTRY_DATABASE_URL")
    monkeypatch.setenv("DAGSENTRY_DATABASE_URL", schema_url)
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        session_factory = create_session_factory(schema_url)
        with session_factory() as session:
            first = bootstrap_admin(
                session,
                email="first-admin@example.com",
                display_name="First Admin",
                password="first secure admin password",
            )
        with session_factory() as session:
            second = create_user(
                session,
                actor_user_id=first.id,
                email="second-admin@example.com",
                display_name="Second Admin",
                role=UserRole.ADMIN,
                temporary_password="second secure admin password",
                correlation_id=None,
            )

        barrier = Barrier(2)

        def demote(target_id: UUID, actor_id: UUID) -> str:
            barrier.wait()
            with session_factory() as session:
                try:
                    update_user(
                        session,
                        actor_user_id=actor_id,
                        user_id=target_id,
                        display_name=None,
                        role=UserRole.OPERATOR,
                        status=None,
                        correlation_id=None,
                    )
                except LastActiveAdminError:
                    return "blocked"
                return "demoted"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda pair: demote(*pair),
                    ((first.id, second.id), (second.id, first.id)),
                )
            )

        assert sorted(results) == ["blocked", "demoted"]
        with session_factory() as session:
            active_admins = session.scalar(
                select(func.count())
                .select_from(UserRecord)
                .where(
                    UserRecord.role == UserRole.ADMIN,
                    UserRecord.status == UserStatus.ACTIVE,
                )
            )
            assert active_admins == 1
        command.downgrade(alembic_config, "base")
    finally:
        if original_url is None:
            monkeypatch.delenv("DAGSENTRY_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DAGSENTRY_DATABASE_URL", original_url)
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
