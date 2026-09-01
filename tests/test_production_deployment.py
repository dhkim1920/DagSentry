from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENTRYPOINT = Path("deployment/container-entrypoint.sh")


def test_container_entrypoint_loads_only_named_dagsentry_secrets(tmp_path: Path) -> None:
    (tmp_path / "DAGSENTRY_DATABASE_URL").write_text(
        "postgresql+psycopg://fixture\n",
        encoding="utf-8",
    )
    (tmp_path / "UNRELATED_SECRET").write_text("must-not-load\n", encoding="utf-8")
    environment = {
        "DAGSENTRY_SECRETS_DIR": str(tmp_path),
        "PATH": os.environ["PATH"],
    }

    result = subprocess.run(
        [
            "/bin/sh",
            str(ENTRYPOINT),
            sys.executable,
            "-c",
            (
                "import os; "
                "assert os.environ['DAGSENTRY_DATABASE_URL'] == "
                "'postgresql+psycopg://fixture'; "
                "assert 'UNRELATED_SECRET' not in os.environ"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "fixture" not in result.stdout
    assert "fixture" not in result.stderr


def test_container_entrypoint_rejects_empty_secret_without_echoing_it(tmp_path: Path) -> None:
    secret_name = "DAGSENTRY_INGEST_API_TOKEN"
    (tmp_path / secret_name).write_text("\n", encoding="utf-8")

    result = subprocess.run(
        ["/bin/sh", str(ENTRYPOINT), sys.executable, "-c", "raise SystemExit(0)"],
        env={"DAGSENTRY_SECRETS_DIR": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 78
    assert result.stdout == ""
    assert result.stderr == f"error: Docker Secret {secret_name} is empty\n"
