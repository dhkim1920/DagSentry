"""Foreground launcher for the separate API and Daily Report scheduler processes."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time

logger = logging.getLogger(__name__)
SERVICES = {"api": "dagsentry.api", "scheduler": "dagsentry.scheduler_app"}
SHUTDOWN_TIMEOUT_SECONDS = 300


def run_api_and_scheduler(*, port: int = 8000) -> int:
    """Keep both services together until a stop signal or either child exits."""
    stopping = False
    processes: dict[str, subprocess.Popen[bytes]] = {}

    def request_stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    previous_handlers = {
        sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for name, module in SERVICES.items():
            if stopping:
                return 0
            try:
                command = [sys.executable, "-c", f"from {module} import main; main()"]
                if name == "api":
                    command.extend(["--port", str(port)])
                process = subprocess.Popen(
                    command,
                    start_new_session=True,
                )
            except OSError:
                logger.error("Unable to start %s process", name)
                return 1
            processes[name] = process
            logger.info("Started %s process pid=%s", name, process.pid)
        while not stopping:
            for name, process in processes.items():
                returncode = process.poll()
                if returncode is not None:
                    logger.error("%s process exited unexpectedly code=%s", name, returncode)
                    return 128 - returncode if returncode < 0 else returncode or 1
            time.sleep(0.1)
        return 0
    finally:
        try:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
            deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
            for name, process in processes.items():
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    logger.warning("%s process exceeded shutdown timeout; terminating", name)
                    process.kill()
                    process.wait()
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
