"""Shared GraphEngine singleton + fixture bootstrap.

Single source of truth for all API routers so ingest, analytics,
evidence, dossier, and graph views always see the same data.
"""

from __future__ import annotations

from app.core.graph_engine import GraphEngine
from app.db.session import Base, SessionLocal, engine as db_engine
from app.services.persistence_service import ensure_db_seeded

engine = GraphEngine()


def ensure_sample_loaded() -> bool:
    """Ensure database tables exist and initial dataset is loaded/hydrated."""
    Base.metadata.create_all(bind=db_engine)
    with SessionLocal() as db:
        return ensure_db_seeded(engine, db)


def get_engine() -> GraphEngine:
    """Return the shared engine."""
    return engine


# Initial setup on module load
ensure_sample_loaded()
