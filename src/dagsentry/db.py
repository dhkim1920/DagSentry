"""Database engine and session construction."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for DagSentry database records."""


SessionFactory = sessionmaker[Session]


def create_session_factory(database_url: str) -> SessionFactory:
    """Create a session factory for one service process."""
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """Provide a transaction-capable session to one API request."""
    with request.app.state.session_factory() as session:
        yield session
