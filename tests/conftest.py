from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import dagsentry.models  # noqa: F401
from dagsentry.config import Settings
from dagsentry.db import Base, SessionFactory


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite://",
        environment="production",
        ingest_api_token=SecretStr("test-ingest-token"),
        viewer_api_token=SecretStr("test-viewer-token"),
        operator_api_token=SecretStr("test-operator-token"),
        operator_api_identity="oncall@example.com",
    )


@pytest.fixture
def session_factory() -> Iterator[SessionFactory]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory: SessionFactory) -> Iterator[Session]:
    with session_factory() as database_session:
        yield database_session
