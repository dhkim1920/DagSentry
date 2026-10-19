from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text

from dagsentry.config import Settings
from dagsentry.db import Base, create_session_factory
from dagsentry.models import WorkerHeartbeatRecord
from dagsentry.routes.operations import _snapshot
from tests.test_worker import add_job

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("crash", [False, True])
def test_real_worker_heartbeat_tracks_busy_process_and_exit(tmp_path: Path, crash: bool) -> None:
    url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    schema = f"worker_liveness_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in url else "?"
    scoped_url = f"{url}{separator}options={quote_plus(f'-csearch_path={schema}')}"
    engine = create_engine(scoped_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(scoped_url)
    add_job(factory)
    release = tmp_path / "release"
    entered = tmp_path / "entered"
    child_script = """
import os
import time
from pathlib import Path
from dagsentry.db import create_session_factory
from dagsentry.worker import DiagnosisWorker, WorkerOptions

def handle(_):
    Path(os.environ['WORKER_ENTERED_FILE']).touch()
    while not Path(os.environ['WORKER_RELEASE_FILE']).exists():
        time.sleep(0.01)

worker = DiagnosisWorker(
    create_session_factory(os.environ['WORKER_TEST_DATABASE_URL']), handle, 'process-test',
    options=WorkerOptions(heartbeat_interval_seconds=0.02, poll_interval_seconds=0.01),
)
worker.install_signal_handlers()
worker.run()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_script],
        env={
            **os.environ,
            "WORKER_TEST_DATABASE_URL": scoped_url,
            "WORKER_ENTERED_FILE": str(entered),
            "WORKER_RELEASE_FILE": str(release),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        first = None
        while time.monotonic() < deadline:
            with factory() as session:
                heartbeat = session.scalar(select(WorkerHeartbeatRecord))
                if entered.exists() and heartbeat is not None:
                    if first is None:
                        first = heartbeat.last_heartbeat_at
                    elif heartbeat.last_heartbeat_at > first:
                        break
            assert child.poll() is None
            time.sleep(0.01)
        else:
            pytest.fail("live busy Worker did not continue heartbeating")
        with factory() as session:
            snapshot = _snapshot(session, Settings(), datetime.now(UTC))
            assert snapshot.worker.status == "ONLINE"
            assert snapshot.worker.online_instances == 1
            assert snapshot.queue.processing == 1
            assert snapshot.worker.last_success_at is None
        if crash:
            child.kill()
        else:
            child.terminate()
            release.touch()
        output = child.communicate(timeout=5)
        assert child.returncode == (-9 if crash else 0), output
        with factory() as session:
            heartbeat = session.scalar(select(WorkerHeartbeatRecord))
            assert heartbeat is not None
            assert (heartbeat.stopped_at is None) is crash
            now = datetime.now(UTC) + timedelta(seconds=31 if crash else 0)
            snapshot = _snapshot(session, Settings(), now)
            assert snapshot.worker.status == "OFFLINE"
            assert snapshot.worker.online_instances == 0
            assert snapshot.queue.processing == (1 if crash else 0)
            assert snapshot.queue.completed == (0 if crash else 1)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        with factory() as session:
            session.connection().engine.dispose()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
