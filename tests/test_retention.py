from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from dagsentry.db import SessionFactory
from dagsentry.models import UserRecord, UserSessionRecord, WorkerHeartbeatRecord
from dagsentry.retention import prune_operational_records
from tests.test_admin_api import bootstrap


@pytest.mark.parametrize("apply", [False, True])
def test_retention_preserves_live_sessions_and_latest_worker_observation(
    session_factory: SessionFactory, apply: bool
) -> None:
    bootstrap(session_factory)
    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    with session_factory.begin() as session:
        user = session.scalar(select(UserRecord))
        assert user is not None
        for index, (expires, revoked) in enumerate(
            [
                (old, None),
                (now + timedelta(days=1), old),
                (now, None),
                (old + timedelta(days=2), None),
            ]
        ):
            session.add(
                UserSessionRecord(
                    user_id=user.id,
                    token_hash=f"{index:064d}",
                    csrf_token_hash=f"{index + 10:064d}",
                    expires_at=expires,
                    revoked_at=revoked,
                )
            )
        for age, stopped in [(32, True), (31, False), (0, False)]:
            session.add(
                WorkerHeartbeatRecord(
                    instance_id=uuid4(),
                    started_at=old - timedelta(days=2),
                    last_heartbeat_at=now - timedelta(days=age),
                    stopped_at=old if stopped else None,
                    version="test",
                )
            )
    with session_factory() as session:
        result = prune_operational_records(session, days=30, apply=apply, now=now)
    assert result["user_sessions"] == 2
    assert result["worker_heartbeats"] == 2
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserSessionRecord)) == (
            2 if apply else 4
        )
        assert session.scalar(select(func.count()).select_from(WorkerHeartbeatRecord)) == (
            1 if apply else 3
        )


def test_retention_is_bounded_and_keeps_last_offline_observation(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        for age in [31, 32, 33, 34]:
            session.add(
                WorkerHeartbeatRecord(
                    instance_id=uuid4(),
                    started_at=now - timedelta(days=40),
                    last_heartbeat_at=now - timedelta(days=age),
                    stopped_at=None,
                    version="test",
                )
            )
    with session_factory() as session:
        assert (
            prune_operational_records(session, days=30, batch_size=2, apply=True, now=now)[
                "worker_heartbeats"
            ]
            == 2
        )
    with session_factory() as session:
        assert (
            prune_operational_records(session, days=30, apply=True, now=now)["worker_heartbeats"]
            == 1
        )
    with session_factory() as session:
        assert (
            prune_operational_records(session, days=30, apply=True, now=now)["worker_heartbeats"]
            == 0
        )
        latest = session.scalar(select(WorkerHeartbeatRecord))
        assert latest is not None
        assert latest.last_heartbeat_at.replace(tzinfo=UTC) == now - timedelta(days=31)


@pytest.mark.parametrize("days,batch_size", [(0, 100), (-1, 100), (30, 0), (30, 10001)])
def test_retention_rejects_invalid_limits(
    session_factory: SessionFactory, days: int, batch_size: int
) -> None:
    with session_factory() as session, pytest.raises(ValueError):
        prune_operational_records(session, days=days, batch_size=batch_size)


def test_retention_respects_longer_offline_threshold(session_factory: SessionFactory) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        for age in [0, 2]:
            session.add(
                WorkerHeartbeatRecord(
                    instance_id=uuid4(),
                    started_at=now - timedelta(days=5),
                    last_heartbeat_at=now - timedelta(days=age),
                    version="test",
                )
            )
    with session_factory() as session:
        result = prune_operational_records(
            session, days=1, now=now, apply=True, worker_offline_after_seconds=3 * 86400
        )
        assert result["worker_heartbeats"] == 0
