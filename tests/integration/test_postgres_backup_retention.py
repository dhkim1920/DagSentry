from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from dagsentry.api import create_app
from dagsentry.backup import create_backup
from dagsentry.config import Settings, get_settings
from dagsentry.connection_crypto import ConnectionSecretCipher, EncryptedConnectionSecret
from dagsentry.models import (
    AdminAuditEventRecord,
    ManagedConnectionRecord,
    UserRecord,
    UserSessionRecord,
    WorkerHeartbeatRecord,
)
from dagsentry.retention import prune_operational_records
from tests.integration.test_postgres_security_boundaries import (
    CONNECTION_SECRET,
    KEY_BYTES,
    _connection_body,
    _csrf_headers,
)
from tests.test_admin_api import ADMIN_PASSWORD, bootstrap, login

pytestmark = pytest.mark.integration


def test_real_backup_restores_authentication_connections_and_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url_text = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if url_text is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    for program in ["psql", "pg_dump", "pg_restore"]:
        if shutil.which(program) is None:
            pytest.skip(f"{program} is not installed")
    url = make_url(url_text)
    source_name, restore_name = f"backup_source_{uuid4().hex}", f"backup_restore_{uuid4().hex}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        server_major = connection.dialect.server_version_info
        assert server_major is not None
        client_version = subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True, check=True
        ).stdout
        version_match = re.search(r"(\d+)\.", client_version)
        assert version_match is not None
        if int(version_match[1]) < server_major[0]:
            admin.dispose()
            pytest.skip("pg_dump is older than the PostgreSQL server; install a compatible client")
    source = create_engine(url.set(database=source_name))
    restored = create_engine(url.set(database=restore_name))
    engines = [source, restored]
    created: list[str] = []
    connection_id = uuid4()
    key = SecretStr(base64.b64encode(KEY_BYTES).decode())
    try:
        with admin.connect() as connection:
            for name in [source_name, restore_name]:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
                created.append(name)
        monkeypatch.setenv(
            "DAGSENTRY_DATABASE_URL", source.url.render_as_string(hide_password=False)
        )
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        factory = sessionmaker(source, expire_on_commit=False)
        bootstrap(factory)
        source_app = create_app(
            Settings(
                database_url=str(source.url),
                connection_encryption_key=key,
                connection_encryption_key_version=7,
            ),
            factory,
        )

        async def configure() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=source_app), base_url="https://test"
            ) as client:
                await login(client, "admin@example.com", ADMIN_PASSWORD)
                response = await client.put(
                    f"/api/v1/admin/connections/{connection_id}",
                    headers=_csrf_headers(client),
                    json=_connection_body(),
                )
                assert response.status_code == 200

        asyncio.run(configure())
        with factory.begin() as session:
            user = session.scalar(select(UserRecord))
            assert user is not None
            session.add(
                UserSessionRecord(
                    user_id=user.id,
                    token_hash="e" * 64,
                    csrf_token_hash="f" * 64,
                    expires_at=datetime.now(UTC) - timedelta(days=60),
                )
            )
            for age in [31, 32, 33]:
                session.add(
                    WorkerHeartbeatRecord(
                        instance_id=uuid4(),
                        started_at=datetime.now(UTC) - timedelta(days=40),
                        last_heartbeat_at=datetime.now(UTC) - timedelta(days=age),
                        version="test",
                    )
                )
        service_file = tmp_path / "pg_service.conf"
        # A private libpq service file keeps credentials out of subprocess arguments.
        service_file.touch(mode=0o600)
        service_file.write_text(
            "[backup-test]\n"
            + "\n".join(
                f"{name}={value}"
                for name, value in {
                    "host": url.host or "",
                    "port": url.port or 5432,
                    "user": url.username or "",
                    "password": url.password or "",
                    "dbname": source_name,
                }.items()
            )
            + "\n"
        )
        monkeypatch.setenv("PGSERVICEFILE", str(service_file))
        target = create_backup(
            tmp_path / "archives", service="backup-test", key_version=7, git_revision="a" * 40
        )
        manifest = json.loads((target / "manifest.json").read_text())
        archive = target / "database.dump"
        assert manifest["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert manifest["alembic_revision"] == "0022"
        assert manifest["restore_verified"] is False
        assert CONNECTION_SECRET not in (target / "manifest.json").read_text()
        restore_env = {
            **os.environ,
            "PGHOST": url.host or "",
            "PGPORT": str(url.port or 5432),
            "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "",
        }
        result = subprocess.run(
            [
                "pg_restore",
                "--no-password",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                restore_name,
                str(archive),
            ],
            env=restore_env,
            capture_output=True,
        )
        assert result.returncode == 0, "Isolated pg_restore failed"
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        restore_app = create_app(
            Settings(
                database_url=str(restored.url),
                connection_encryption_key=key,
                connection_encryption_key_version=7,
            ),
            restored_factory,
        )

        async def verify() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restore_app), base_url="https://test"
            ) as client:
                await login(client, "admin@example.com", ADMIN_PASSWORD)
                response = await client.get(f"/api/v1/admin/connections/{connection_id}")
                assert response.status_code == 200
                assert response.json()["secret_configured"] is True

        asyncio.run(verify())
        with restored_factory() as session:
            assert (
                session.scalar(select(text("version_num")).select_from(text("alembic_version")))
                == "0022"
            )
            record = session.get(ManagedConnectionRecord, connection_id)
            assert record is not None and record.secret_ciphertext is not None
            assert record.secret_nonce is not None and record.secret_key_version is not None
            decrypted = ConnectionSecretCipher(KEY_BYTES, 7).decrypt(
                EncryptedConnectionSecret(
                    record.secret_ciphertext, record.secret_nonce, record.secret_key_version
                ),
                connection_id=record.id,
                environment=record.environment,
                purpose=record.purpose,
                provider=record.provider,
            )
            assert decrypted == {"bot_token": CONNECTION_SECRET}
            assert session.scalar(select(AdminAuditEventRecord)) is not None
        with restored_factory() as session:
            preview = prune_operational_records(session, days=30)
            assert preview["user_sessions"] == 1 and preview["worker_heartbeats"] == 2
        with restored_factory() as session:
            applied = prune_operational_records(session, days=30, apply=True)
            assert applied["user_sessions"] == 1 and applied["worker_heartbeats"] == 2
        asyncio.run(verify())  # Active sessions, users and connection history survive cleanup.
    finally:
        get_settings.cache_clear()
        for engine in engines:
            engine.dispose()
        with admin.connect() as connection:
            for name in created:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
