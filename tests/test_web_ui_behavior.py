from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_web_ui_diagnosis_selection_and_filter_navigation() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for Web UI behavior tests")
    result = subprocess.run(
        [
            node,
            "--experimental-vm-modules",
            "--test",
            str(Path(__file__).with_name("web_ui_behavior.cjs")),
            str(Path(__file__).with_name("web_ui_modules.cjs")),
            str(Path(__file__).with_name("web_ui_request_races.cjs")),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
