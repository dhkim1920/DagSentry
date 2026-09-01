import asyncio
from uuid import UUID

import httpx
from sqlalchemy.orm import sessionmaker

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory


def test_liveness(settings: Settings, session_factory: SessionFactory) -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/live")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.3.0.dev0"}
    assert UUID(response.headers["X-Correlation-ID"])


def test_readiness(settings: Settings, session_factory: SessionFactory) -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/ready")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_when_database_is_unavailable(settings: Settings) -> None:
    unavailable_factory = sessionmaker(bind=None)

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, unavailable_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/ready")

    response = asyncio.run(request())

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}


def test_valid_correlation_id_is_returned(
    settings: Settings, session_factory: SessionFactory
) -> None:
    correlation_id = "d8dce2a3-d335-429c-a3e2-37f1d2bfe182"

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/live", headers={"X-Correlation-ID": correlation_id})

    response = asyncio.run(request())

    assert response.headers["X-Correlation-ID"] == correlation_id
