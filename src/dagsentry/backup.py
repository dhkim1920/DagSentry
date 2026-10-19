"""Create private PostgreSQL archives with checksums and recovery metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dagsentry import __version__


class BackupError(RuntimeError):
    """Safe backup failure; PostgreSQL stderr can contain credentials and is not exposed."""


def _run(arguments: list[str], service: str) -> str:
    try:
        return subprocess.run(
            arguments,
            env={**os.environ, "PGSERVICE": service},
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise BackupError(
            f"{arguments[0]} failed; check service configuration and permissions"
        ) from error


def create_backup(directory: Path, *, service: str, key_version: int, git_revision: str) -> Path:
    """Publish a complete archive directory atomically; do not alter older backups."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", service):
        raise ValueError(
            "service must be a PostgreSQL service name (1–64 letters, digits, _, . or -)"
        )
    if key_version < 1:
        raise ValueError("key_version must be a positive version number, never the encryption key")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", git_revision):
        raise ValueError(
            "git_revision must be the deployed Git commit (7–40 hexadecimal characters)"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dagsentry-", dir=directory))
    try:
        revision = _run(
            [
                "psql",
                "-X",
                "--no-password",
                "--set",
                "ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT version_num FROM alembic_version",
            ],
            service,
        )
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", revision):
            raise BackupError(
                "Expected one Alembic revision; migrate or check the database service"
            )
        archive = staging / "database.dump"
        archive.touch(mode=0o600, exist_ok=False)
        _run(
            [
                "pg_dump",
                "--no-password",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(archive),
            ],
            service,
        )
        _run(["pg_restore", "--list", str(archive)], service)
        with archive.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        now = datetime.now(UTC)
        manifest = staging / "manifest.json"
        manifest.touch(mode=0o600, exist_ok=False)
        manifest.write_text(
            json.dumps(
                {
                    "application_version": __version__,
                    "git_revision": git_revision,
                    "alembic_revision": revision,
                    "created_at": now.isoformat(),
                    "connection_encryption_key_version": key_version,
                    "archive": archive.name,
                    "sha256": checksum,
                    "restore_verified": False,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        target = directory / f"dagsentry-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex}"
        staging.rename(target)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dagsentry-backup")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--service",
        required=True,
        help="PostgreSQL service name; use a service/pass file for credentials",
    )
    parser.add_argument(
        "--key-version",
        type=int,
        required=True,
        help="Matching connection encryption key version, never the key",
    )
    parser.add_argument(
        "--git-revision", required=True, help="Git commit of the deployed application"
    )
    arguments = parser.parse_args(argv)
    try:
        target = create_backup(
            arguments.directory,
            service=arguments.service,
            key_version=arguments.key_version,
            git_revision=arguments.git_revision,
        )
    except ValueError as error:
        parser.error(str(error))
    except BackupError as error:
        parser.exit(1, f"Backup failed: {error}\n")
    except OSError:
        parser.exit(1, "Backup failed: filesystem operation failed\n")
    print(json.dumps({"backup_directory": str(target), "restore_verified": False}, sort_keys=True))


if __name__ == "__main__":
    main()
