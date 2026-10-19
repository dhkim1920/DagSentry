from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from dagsentry import models
from dagsentry.db import Base, create_session_factory
from dagsentry.worker import DiagnosisWorker, WorkerOptions
from tests.test_worker import add_job


@pytest.mark.parametrize("fail_first_heartbeat", [False, True])
def test_busy_worker_heartbeats_independently_and_records_shutdown(
    tmp_path: Path,
    fail_first_heartbeat: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = create_session_factory(f"sqlite+pysqlite:///{tmp_path / 'heartbeat.sqlite3'}")
    with factory() as session:
        engine = session.connection().engine
        Base.metadata.create_all(engine)
    add_job(factory)
    entered = Event()
    release = Event()
    errors: list[BaseException] = []

    def handle(_: object) -> None:
        entered.set()
        assert release.wait(5)

    worker = DiagnosisWorker(
        factory,
        handle,
        "heartbeat-test",
        options=WorkerOptions(
            heartbeat_interval_seconds=0.02,
            poll_interval_seconds=0.01,
        ),
    )

    def run() -> None:
        try:
            worker.run()
        except BaseException as error:
            errors.append(error)

    def reject_first_insert(*_: object) -> None:
        nonlocal fail_first_heartbeat
        if fail_first_heartbeat:
            fail_first_heartbeat = False
            raise SQLAlchemyError("private-database-token")

    event.listen(models.WorkerHeartbeatRecord, "before_insert", reject_first_insert)
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        deadline = time.monotonic() + 5
        first = None
        while time.monotonic() < deadline:
            with factory() as session:
                heartbeat = session.scalar(select(models.WorkerHeartbeatRecord))
                if heartbeat is not None:
                    if first is None:
                        first = heartbeat.last_heartbeat_at
                        worker.request_stop()  # Still alive while the owned job drains.
                    elif heartbeat.last_heartbeat_at > first:
                        assert heartbeat.stopped_at is None
                        break
            Event().wait(0.01)
        else:
            pytest.fail("heartbeat did not advance while the job was running")
    finally:
        release.set()
        worker.request_stop()
        thread.join(timeout=5)
        event.remove(models.WorkerHeartbeatRecord, "before_insert", reject_first_insert)
    assert not thread.is_alive()
    assert not errors
    with factory() as session:
        heartbeat = session.scalar(select(models.WorkerHeartbeatRecord))
        assert heartbeat is not None and heartbeat.stopped_at is not None
        assert heartbeat.instance_id == worker.instance_id
    assert "private-database-token" not in caplog.text
    engine.dispose()
