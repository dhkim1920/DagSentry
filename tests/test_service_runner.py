from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dagsentry import api, service_runner


def test_api_without_option_still_runs_only_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dagsentry-api"])
    with (
        patch("dagsentry.api.uvicorn.run") as server,
        patch.object(api, "run_api_and_scheduler") as together,
    ):
        api.main()
    server.assert_called_once_with("dagsentry.api:app", host="0.0.0.0", port=8000, log_config=None)
    together.assert_not_called()


def test_api_option_uses_separate_process_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dagsentry-api", "--with-scheduler"])
    with (
        patch("dagsentry.api.uvicorn.run") as server,
        patch.object(api, "run_api_and_scheduler", return_value=7) as together,
    ):
        with pytest.raises(SystemExit) as exited:
            api.main()
    assert exited.value.code == 7
    together.assert_called_once_with(port=8000)
    server.assert_not_called()


@pytest.mark.parametrize("with_scheduler", [False, True])
def test_custom_api_port_is_used_by_direct_and_joint_execution(
    monkeypatch: pytest.MonkeyPatch, with_scheduler: bool
) -> None:
    arguments = ["dagsentry-api", "--port", "8001"]
    if with_scheduler:
        arguments.append("--with-scheduler")
    monkeypatch.setattr(sys, "argv", arguments)
    with (
        patch("dagsentry.api.uvicorn.run") as server,
        patch.object(api, "run_api_and_scheduler", return_value=0) as together,
    ):
        if with_scheduler:
            with pytest.raises(SystemExit) as exited:
                api.main()
            assert exited.value.code == 0
            together.assert_called_once_with(port=8001)
            server.assert_not_called()
        else:
            api.main()
            server.assert_called_once_with(
                "dagsentry.api:app", host="0.0.0.0", port=8001, log_config=None
            )
            together.assert_not_called()


@pytest.mark.parametrize("port", ["0", "65536"])
def test_invalid_port_is_rejected_before_starting_services(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["dagsentry-api", "--with-scheduler", "--port", port])
    with (
        patch("dagsentry.api.uvicorn.run") as server,
        patch.object(api, "run_api_and_scheduler") as together,
    ):
        with pytest.raises(SystemExit) as exited:
            api.main()
        assert exited.value.code == 2
        server.assert_not_called()
        together.assert_not_called()


def test_joint_launcher_forwards_port_only_to_the_api_child() -> None:
    with patch("dagsentry.service_runner.subprocess.Popen") as start:
        start.return_value.poll.return_value = 1
        service_runner.run_api_and_scheduler(port=8001)
    api_command = start.call_args_list[0].args[0]
    scheduler_command = start.call_args_list[1].args[0]
    assert api_command[0] == scheduler_command[0] == sys.executable
    assert api_command[-2:] == ["--port", "8001"]
    assert "--with-scheduler" not in api_command
    assert "--port" not in scheduler_command


def start_runner(
    tmp_path: Path, *, exit_code: int | None = None, ignore_stop: bool = False
) -> subprocess.Popen[str]:
    for name in ["api", "scheduler"]:
        (tmp_path / f"test_{name}.py").write_text(f'''
import os, signal, time
from pathlib import Path

def main():
    root = Path(os.environ["TEST_SERVICE_ROOT"])
    running = True
    def stop(*args):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, signal.SIG_IGN if {name == "scheduler" and ignore_stop!r} else stop)
    (root / "{name}.pid").write_text(str(os.getpid()))
    while running:
        if {name == "api" and exit_code is not None!r} and (root / "exit").exists():
            raise SystemExit({exit_code!r})
        time.sleep(0.01)
    (root / "{name}.stopped").touch()
''')
    environment = {
        **os.environ,
        "TEST_SERVICE_ROOT": str(tmp_path),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")]),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from dagsentry import service_runner as runner; "
            "runner.SERVICES = {'api': 'test_api', 'scheduler': 'test_scheduler'}; "
            "runner.SHUTDOWN_TIMEOUT_SECONDS = 0.2; "
            "raise SystemExit(runner.run_api_and_scheduler())",
        ],
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not all((tmp_path / f"{name}.pid").exists() for name in ["api", "scheduler"]):
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            output = process.communicate(timeout=5)
            pytest.fail(f"test services did not start: {output}")
        time.sleep(0.01)
    return process


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_parent_signal_stops_both_separate_children(tmp_path: Path, stop_signal: int) -> None:
    process = start_runner(tmp_path)
    try:
        children = [int((tmp_path / f"{name}.pid").read_text()) for name in ["api", "scheduler"]]
        assert len({process.pid, *children}) == 3
        process.send_signal(stop_signal)
        output = process.communicate(timeout=5)
        assert process.returncode == 0, output
        assert all((tmp_path / f"{name}.stopped").exists() for name in ["api", "scheduler"])
        for pid in children:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


@pytest.mark.parametrize("exit_code", [0, 9])
def test_unexpected_child_exit_stops_sibling_and_returns_failure(
    tmp_path: Path, exit_code: int
) -> None:
    process = start_runner(tmp_path, exit_code=exit_code)
    try:
        (tmp_path / "exit").touch()
        output = process.communicate(timeout=5)
        assert process.returncode == (exit_code or 1), output
        assert (tmp_path / "scheduler.stopped").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


def test_shutdown_kills_and_reaps_child_that_ignores_termination(tmp_path: Path) -> None:
    process = start_runner(tmp_path, ignore_stop=True)
    try:
        pid = int((tmp_path / "scheduler.pid").read_text())
        process.terminate()
        output = process.communicate(timeout=5)
        assert process.returncode == 0, output
        assert (tmp_path / "api.stopped").exists()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


def test_second_process_start_failure_cleans_up_first_and_restores_handlers() -> None:
    previous = {sig: signal.getsignal(sig) for sig in [signal.SIGINT, signal.SIGTERM]}
    with patch("dagsentry.service_runner.subprocess.Popen") as start:
        child = start.return_value
        child.poll.return_value = None
        start.side_effect = [child, OSError("process cannot start")]
        assert service_runner.run_api_and_scheduler() == 1
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once()
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
