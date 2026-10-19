"""Bounded cleanup of expired operational records; domain history is retained."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dagsentry.config import get_settings
from dagsentry.db import create_session_factory
from dagsentry.models import UserSessionRecord, WorkerHeartbeatRecord


def prune_operational_records(
    session: Session,
    *,
    days: int,
    batch_size: int = 1000,
    apply: bool = False,
    now: datetime | None = None,
    worker_offline_after_seconds: float = 30,
) -> dict[str, object]:
    """Preview or delete one batch per table, retaining the latest Worker observation."""
    if days < 1 or not 1 <= batch_size <= 10000 or worker_offline_after_seconds <= 0:
        raise ValueError("days must be positive; batch_size must be between 1 and 10000")
    observed = now or datetime.now(UTC)
    cutoff = observed - timedelta(days=days)
    worker_cutoff = min(cutoff, observed - timedelta(seconds=worker_offline_after_seconds))
    latest_worker = (
        select(WorkerHeartbeatRecord.instance_id)
        .order_by(WorkerHeartbeatRecord.last_heartbeat_at.desc(), WorkerHeartbeatRecord.instance_id)
        .limit(1)
        .scalar_subquery()
    )
    result: dict[str, object] = {
        "dry_run": not apply,
        "cutoff": cutoff.isoformat(),
        "batch_size": batch_size,
    }
    with session.begin():
        for name, identity, age, eligible in [
            (
                "user_sessions",
                UserSessionRecord.id,
                UserSessionRecord.expires_at,
                or_(
                    UserSessionRecord.expires_at < cutoff,
                    UserSessionRecord.revoked_at < cutoff,
                ),
            ),
            (
                "worker_heartbeats",
                WorkerHeartbeatRecord.instance_id,
                WorkerHeartbeatRecord.last_heartbeat_at,
                (WorkerHeartbeatRecord.last_heartbeat_at < worker_cutoff)
                & (WorkerHeartbeatRecord.instance_id != latest_worker),
            ),
        ]:
            query = select(identity).where(eligible).order_by(age, identity).limit(batch_size)
            if apply:
                query = query.with_for_update(skip_locked=True)
            identifiers = session.scalars(query).all()
            if apply and identifiers:
                # Repeat eligibility to protect against a concurrent heartbeat on SQLite.
                deleted = session.execute(
                    delete(identity.class_)
                    .where(identity.in_(identifiers), eligible)
                    .returning(identity)
                ).all()
                result[name] = len(deleted)
            else:
                result[name] = len(identifiers)
    return result


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dagsentry-retention")
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="Retain at least this many days after expiry/revocation or last heartbeat",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Maximum rows per table per invocation (1–10000)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Delete eligible records; default is a preview"
    )
    arguments = parser.parse_args(argv)
    settings = get_settings()
    factory = create_session_factory(settings.database_url)
    try:
        with factory() as session:
            result = prune_operational_records(
                session,
                days=arguments.days,
                batch_size=arguments.batch_size,
                apply=arguments.apply,
                worker_offline_after_seconds=settings.worker_offline_after_seconds,
            )
    except ValueError as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.exit(1, "Retention failed: database operation failed\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
