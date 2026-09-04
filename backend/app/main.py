"""FastAPI application entrypoint.

Keeps HTTP concerns here only. All graph logic lives in
app.core.graph_engine, app.db, and app.services.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.analytics import router as analytics_router
from app.api.dossier import router as dossier_router
from app.api.evidence import router as evidence_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.db.models import DBNode
from app.db.session import Base, SessionLocal, engine as db_engine, get_db
from app.models.graph_models import GraphPayload
from app.services.graph_store import engine
from app.services.persistence_service import (
    hydrate_graph_from_db,
    save_payload_to_db,
)


def init_db_and_graph() -> None:
    """Create tables if missing, seed if empty, and hydrate the in-memory GraphEngine."""
    Base.metadata.create_all(bind=db_engine)
    with SessionLocal() as db:
        first_node = db.query(DBNode).first()
        if first_node is None:
            candidates = [
                Path(__file__).resolve().parents[3] / "data" / "clean_graph.json",
                Path(__file__).resolve().parents[2] / "data" / "clean_graph.json",
                Path("data/clean_graph.json"),
                Path("../data/clean_graph.json"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    payload_data = json.loads(candidate.read_text(encoding="utf-8"))
                    raw_nodes = payload_data.get("nodes", [])
                    raw_edges = payload_data.get("edges", [])
                    save_payload_to_db(raw_nodes, raw_edges, db)
                    break
        hydrate_graph_from_db(engine, db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for FastAPI startup and shutdown."""
    init_db_and_graph()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Graph Intelligence API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_db_and_graph()

    app.include_router(health_router)
    app.include_router(graph_router)
    app.include_router(analytics_router)
    app.include_router(evidence_router)
    app.include_router(dossier_router)

    @app.post("/api/ingest")
    def ingest(
        payload: GraphPayload,
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        """Dynamically ingest new records, persist to SQLite, and update in-memory GraphEngine."""
        # 1. Persist to disk in SQLite
        save_payload_to_db(payload.nodes, payload.edges, db)

        # 2. Add into in-memory GraphEngine
        for node in payload.nodes:
            engine.add_node(node)
        for edge in payload.edges:
            engine.add_edge(edge)

        # 3. Recompute metrics on updated graph
        engine.compute_metrics()

        # 4. Return exact expected contract
        return {"status": "loaded", "stats": engine.get_stats()}

    return app


app = create_app()
