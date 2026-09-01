import base64
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine

import dagsentry.admin_app
from dagsentry.admin_app import (
    _read_confirmed_password,
    _read_rotation_keys,
    _run_key_rotation,
    build_parser,
)
from dagsentry.config import Settings
from dagsentry.connection_management import ConnectionManagementError
from dagsentry.db import Base
from dagsentry.identity import IdentityError


def test_admin_parser_has_no_password_argument() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "users",
                "bootstrap",
                "--email",
                "admin@example.com",
                "--display-name",
                "Admin",
                "--password",
                "must-not-be-accepted",
            ]
        )


def test_password_confirmation_must_match() -> None:
    answers = iter(["first secure password", "second secure password"])

    with pytest.raises(IdentityError, match="confirmation"):
        _read_confirmed_password(lambda _: next(answers))


def test_rotation_parser_has_versions_but_no_key_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()

    arguments = parser.parse_args(
        [
            "connections",
            "rotate-key",
            "--current-version",
            "1",
            "--new-version",
            "2",
        ]
    )

    assert arguments.current_version == 1
    assert arguments.new_version == 2
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "connections",
                "rotate-key",
                "--current-version",
                "1",
                "--new-version",
                "2",
                "--new-key",
                "must-not-be-accepted",
            ]
        )
    assert "must-not-be-accepted" not in capsys.readouterr().err


def test_rotation_keys_are_hidden_reader_values_and_new_key_must_be_confirmed() -> None:
    current = base64.b64encode(b"a" * 32).decode()
    new = base64.b64encode(b"b" * 32).decode()
    answers = iter([current, new, new])

    assert _read_rotation_keys(lambda _: next(answers)) == (current, new)

    mismatch = iter([current, new, current])
    with pytest.raises(ConnectionManagementError, match="confirmation"):
        _read_rotation_keys(lambda _: next(mismatch))
    unchanged = iter([current, current, current])
    with pytest.raises(ConnectionManagementError, match="must differ"):
        _read_rotation_keys(lambda _: next(unchanged))


def test_rotation_cli_reports_only_non_secret_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'rotation.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        dagsentry.admin_app,
        "get_settings",
        lambda: Settings(database_url=database_url),
    )
    parser = build_parser()
    arguments = parser.parse_args(
        [
            "connections",
            "rotate-key",
            "--current-version",
            "1",
            "--new-version",
            "2",
        ]
    )
    current = base64.b64encode(b"a" * 32).decode()
    new = base64.b64encode(b"b" * 32).decode()
    answers = iter([current, new, new])

    _run_key_rotation(parser, arguments, lambda _: next(answers))

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload == {
        "connections_rotated": 0,
        "correlation_id": payload["correlation_id"],
        "current_version": 1,
        "new_version": 2,
    }
    UUID(payload["correlation_id"])
    assert current not in output
    assert new not in output
    engine.dispose()
