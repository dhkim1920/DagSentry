import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from dagsentry.backup import BackupError, create_backup


@pytest.mark.parametrize("failure", [None, "psql", "pg_dump", "pg_restore"])
def test_backup_publishes_only_complete_private_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "private-password" not in " ".join(arguments)
        if arguments[0] == failure:
            raise subprocess.CalledProcessError(1, arguments, stderr="private-password")
        if arguments[0] == "pg_dump":
            path = Path(arguments[arguments.index("--file") + 1])
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            path.write_bytes(b"archive-fixture")
        return subprocess.CompletedProcess(arguments, 0, "0022\n" if arguments[0] == "psql" else "")

    mocked = Mock(side_effect=run)
    monkeypatch.setattr("dagsentry.backup.subprocess.run", mocked)
    if failure:
        with pytest.raises(BackupError, match=f"{failure} failed") as error:
            create_backup(tmp_path, service="test", key_version=1, git_revision="a" * 40)
        assert "private-password" not in str(error.value)
        assert list(tmp_path.iterdir()) == []
        return
    target = create_backup(tmp_path, service="test", key_version=1, git_revision="a" * 40)
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["alembic_revision"] == "0022"
    assert manifest["sha256"] == hashlib.sha256(b"archive-fixture").hexdigest()
    assert manifest["connection_encryption_key_version"] == 1
    assert manifest["git_revision"] == "a" * 40
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert [call.args[0][0] for call in mocked.call_args_list] == ["psql", "pg_dump", "pg_restore"]


def test_backup_rejects_unversioned_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "dagsentry.backup.subprocess.run", Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    )
    with pytest.raises(BackupError, match="Alembic revision"):
        create_backup(tmp_path, service="test", key_version=1, git_revision="a" * 40)
    assert list(tmp_path.iterdir()) == []
