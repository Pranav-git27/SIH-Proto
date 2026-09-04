"""Synchronous SQLAlchemy 2.0 database engine and session configuration.

Backed by SQLite ("sqlite:///./criminal_network.db") with check_same_thread=False
to safely service FastAPI requests without greenlet/asyncio event loop deadlocks.
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

DATABASE_URL = "sqlite:///./criminal_network.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Declarative base class for SQLAlchemy 2.0 ORM models."""
    pass


def get_db() -> Generator[Session, None, None]:
    """Provide a safe transactional database session scope."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
