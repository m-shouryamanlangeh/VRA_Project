"""SQLAlchemy engine, session factory, and database initialization."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


def _create_engine():
    url = settings.resolved_database_url()
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args)


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """
    Create database tables if they do not exist.

    Imports models so they are registered on ``Base.metadata`` before
    ``create_all``.
    """
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped use (FastAPI dependency)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
