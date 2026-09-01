"""Interactive local account and Managed Connection recovery commands."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from dagsentry.config import get_settings
from dagsentry.connection_crypto import ConnectionEncryptionError, ConnectionSecretCipher
from dagsentry.connection_management import (
    ConnectionManagementError,
    rotate_connection_secrets,
)
from dagsentry.db import create_session_factory
from dagsentry.identity import IdentityError, bootstrap_admin, reset_admin_password


def build_parser() -> argparse.ArgumentParser:
    """Build the Admin CLI without accepting passwords as arguments."""
    parser = argparse.ArgumentParser(prog="dagsentry-admin")
    resources = parser.add_subparsers(dest="resource", required=True)
    users = resources.add_parser("users", help="Manage local users")
    commands = users.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap", help="Create the initial Admin")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)

    reset = commands.add_parser("reset-password", help="Recover one Admin password")
    reset.add_argument("email")

    connections = resources.add_parser("connections", help="Manage encrypted connections")
    connection_commands = connections.add_subparsers(dest="command", required=True)
    rotate = connection_commands.add_parser(
        "rotate-key",
        help="Atomically re-encrypt all Managed Connection Secrets",
    )
    rotate.add_argument("--current-version", required=True, type=_positive_integer)
    rotate.add_argument("--new-version", required=True, type=_positive_integer)
    rotate.add_argument(
        "--current-key",
        action=_RejectSecretArgument,
        help=argparse.SUPPRESS,
    )
    rotate.add_argument(
        "--new-key",
        action=_RejectSecretArgument,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one interactive Admin identity command."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.exit(2, "error: secret input requires an interactive terminal\n")
    if arguments.resource == "connections":
        _run_key_rotation(parser, arguments, getpass.getpass)
    else:
        _run_user_command(parser, arguments, getpass.getpass)


def _read_confirmed_password(reader: Callable[[str], str]) -> str:
    first = reader("Password: ")
    second = reader("Confirm password: ")
    if first != second:
        raise IdentityError("password confirmation does not match")
    return first


def _read_rotation_keys(reader: Callable[[str], str]) -> tuple[str, str]:
    current_key = reader("Current encryption key (Base64): ")
    new_key = reader("New encryption key (Base64): ")
    confirmation = reader("Confirm new encryption key: ")
    if new_key != confirmation:
        raise ConnectionManagementError("new encryption key confirmation does not match")
    if current_key == new_key:
        raise ConnectionManagementError("new encryption key must differ from the current key")
    return current_key, new_key


def _run_user_command(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
    reader: Callable[[str], str],
) -> None:
    try:
        password = _read_confirmed_password(reader)
        session_factory = create_session_factory(get_settings().database_url)
        with session_factory() as session:
            if arguments.command == "bootstrap":
                user = bootstrap_admin(
                    session,
                    email=arguments.email,
                    display_name=arguments.display_name,
                    password=password,
                )
            else:
                user = reset_admin_password(
                    session,
                    email=arguments.email,
                    password=password,
                )
    except IdentityError as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({"email": user.email, "role": user.role, "user_id": str(user.id)}))


def _run_key_rotation(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
    reader: Callable[[str], str],
) -> None:
    try:
        current_key, new_key = _read_rotation_keys(reader)
        old_cipher = ConnectionSecretCipher.from_base64(current_key, arguments.current_version)
        new_cipher = ConnectionSecretCipher.from_base64(new_key, arguments.new_version)
        correlation_id = str(uuid4())
        session_factory = create_session_factory(get_settings().database_url)
        with session_factory() as session:
            rotated = rotate_connection_secrets(
                session,
                old_cipher=old_cipher,
                new_cipher=new_cipher,
                correlation_id=correlation_id,
            )
    except (ConnectionEncryptionError, ConnectionManagementError) as error:
        parser.exit(1, f"error: {error}\n")
    except SQLAlchemyError:
        parser.exit(1, "error: database key rotation failed\n")
    print(
        json.dumps(
            {
                "connections_rotated": rotated,
                "correlation_id": correlation_id,
                "current_version": arguments.current_version,
                "new_version": arguments.new_version,
            }
        )
    )


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("version must be positive")
    return parsed


class _RejectSecretArgument(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str] | None,
        option_string: str | None = None,
    ) -> None:
        raise argparse.ArgumentError(
            self,
            "encryption keys must be entered at the hidden interactive prompts",
        )
