"""Database persistence and graph hydration service for the AI Criminal Network Analysis System.

Provides:
- save_payload_to_db: Safe transactional persistence and upsert of nodes/edges.
- hydrate_graph_from_db: Fast hydration of the in-memory NetworkX GraphEngine from SQLite.
- ensure_db_seeded: Automatic initial seeding from data/clean_graph.json on first boot.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
import uuid

from sqlalchemy.orm import Session

from app.core.graph_engine import GraphEngine
from app.db.models import DBEdge, DBNode


def _parse_meta(val: Any) -> dict[str, Any]:
    """Ensure metadata is safely deserialized into a dictionary."""
    if not val:
        return {}
    if isinstance(val, dict):
        return dict(val)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def save_payload_to_db(nodes: Iterable[Any], edges: Iterable[Any], db: Session) -> None:
    """Persist nodes and edges into SQLite with transactional safety.

    - Nodes: upsert matching on id; updates type/label and merges metadata_json.
    - Edges: idempotent insertion matching on source_id + target_id + type + timestamp.
    """
    try:
        # 1. Process Nodes
        for node in nodes:
            if hasattr(node, "id"):
                node_id = str(node.id)
                node_type = str(node.type)
                node_label = str(node.label)
                if hasattr(node, "metadata"):
                    meta_data = (
                        node.metadata.model_dump()
                        if hasattr(node.metadata, "model_dump")
                        else dict(node.metadata)
                    )
                    if hasattr(node.metadata, "model_extra") and node.metadata.model_extra:
                        meta_data.update(node.metadata.model_extra)
                else:
                    meta_data = {}
            else:
                node_id = str(node.get("id"))
                node_type = str(node.get("type", "Unknown"))
                node_label = str(node.get("label", node_id))
                meta_data = dict(node.get("metadata", {}))
                for k, v in node.items():
                    if k not in {"id", "type", "label", "metadata"}:
                        meta_data[k] = v

            db_node = db.query(DBNode).filter(DBNode.id == node_id).first()
            if db_node:
                db_node.type = node_type
                db_node.label = node_label
                existing_meta = _parse_meta(db_node.metadata_json)
                existing_meta.update(meta_data)
                db_node.metadata_json = json.dumps(existing_meta, default=str)
            else:
                db_node = DBNode(
                    id=node_id,
                    type=node_type,
                    label=node_label,
                    metadata_json=json.dumps(meta_data, default=str),
                )
                db.add(db_node)

        # Flush nodes so foreign keys can reference them
        db.flush()

        # 2. Process Edges
        for edge in edges:
            if hasattr(edge, "source"):
                src = str(edge.source)
                tgt = str(edge.target)
                edge_type = str(edge.type)
                edge_meta = (
                    edge.metadata.model_dump(exclude_none=True)
                    if hasattr(edge.metadata, "model_dump")
                    else dict(edge.metadata)
                )
                if hasattr(edge.metadata, "model_extra") and edge.metadata.model_extra:
                    edge_meta.update(edge.metadata.model_extra)
                edge_id = getattr(edge, "id", None) or getattr(edge, "edge_id", None)
            else:
                src = str(edge.get("source"))
                tgt = str(edge.get("target"))
                edge_type = str(edge.get("type", "Unknown"))
                edge_meta = dict(edge.get("metadata", {}))
                edge_id = edge.get("id") or edge.get("edge_id")
                for k, v in edge.items():
                    if k not in {"source", "target", "type", "metadata", "id", "edge_id"}:
                        edge_meta[k] = v

            # Extract structured columns
            raw_ts = edge_meta.pop("timestamp", None)
            if raw_ts is None and isinstance(edge, dict):
                raw_ts = edge.get("timestamp")
            if isinstance(raw_ts, datetime):
                ts = raw_ts.isoformat()
            elif raw_ts is not None:
                ts = str(raw_ts)
            else:
                ts = None

            raw_amt = edge_meta.pop("amount", None)
            if raw_amt is None and isinstance(edge, dict):
                raw_amt = edge.get("amount")
            amount = float(raw_amt) if raw_amt is not None else None

            raw_dur = edge_meta.pop("duration", None)
            if raw_dur is None and isinstance(edge, dict):
                raw_dur = edge.get("duration")
            duration = int(raw_dur) if raw_dur is not None else None

            ev_src = edge_meta.pop("evidence_source", None)
            if ev_src is None and isinstance(edge, dict):
                ev_src = edge.get("evidence_source")
            if ev_src is None:
                ev_src = edge_meta.get("fir_no")

            # Ensure endpoint nodes exist in DB to prevent foreign key errors
            for endpoint_id in (src, tgt):
                if not db.query(DBNode).filter(DBNode.id == endpoint_id).first():
                    db.add(
                        DBNode(
                            id=endpoint_id,
                            type="Unknown",
                            label=endpoint_id,
                            metadata_json="{}",
                        )
                    )
                    db.flush()

            # Idempotent match: source + target + type + timestamp
            existing_edge = (
                db.query(DBEdge)
                .filter(
                    DBEdge.source_id == src,
                    DBEdge.target_id == tgt,
                    DBEdge.type == edge_type,
                    DBEdge.timestamp == ts,
                )
                .first()
            )

            if existing_edge:
                existing_meta = _parse_meta(existing_edge.metadata_json)
                existing_meta.update(edge_meta)
                existing_edge.metadata_json = json.dumps(existing_meta, default=str)
                if amount is not None:
                    existing_edge.amount = amount
                if duration is not None:
                    existing_edge.duration = duration
                if ev_src is not None:
                    existing_edge.evidence_source = ev_src
            else:
                if not edge_id:
                    edge_id = f"edge_{uuid.uuid4().hex}"
                new_edge = DBEdge(
                    id=str(edge_id),
                    source_id=src,
                    target_id=tgt,
                    type=edge_type,
                    timestamp=ts,
                    amount=amount,
                    duration=duration,
                    evidence_source=ev_src,
                    metadata_json=json.dumps(edge_meta, default=str),
                )
                db.add(new_edge)

        db.commit()
    except Exception:
        db.rollback()
        raise


def hydrate_graph_from_db(engine: GraphEngine, db: Session) -> None:
    """Rebuild in-memory GraphEngine directly from DBNode and DBEdge records.

    Clears the existing in-memory graph, loads all nodes and edges from SQLite,
    and runs compute_metrics() to immediately calculate centrality and communities.
    """
    engine.clear_graph()

    # 1. Read and add all nodes
    db_nodes = db.query(DBNode).all()
    for node in db_nodes:
        attrs = _parse_meta(node.metadata_json)
        attrs["type"] = node.type
        attrs["label"] = node.label
        engine.graph.add_node(node.id, **attrs)

    # 2. Read and add all edges
    db_edges = db.query(DBEdge).all()
    for edge in db_edges:
        attrs = _parse_meta(edge.metadata_json)
        attrs["type"] = edge.type
        attrs["edge_id"] = edge.id
        if edge.timestamp is not None:
            attrs["timestamp"] = edge.timestamp
        if edge.amount is not None:
            attrs["amount"] = edge.amount
        if edge.duration is not None:
            attrs["duration"] = edge.duration
        if edge.evidence_source is not None:
            attrs["evidence_source"] = edge.evidence_source

        engine.graph.add_edge(edge.source_id, edge.target_id, key=edge.id, **attrs)

    # 3. Recalculate metrics on hydrated topology
    engine.compute_metrics()


def ensure_db_seeded(engine: GraphEngine, db: Session) -> bool:
    """Check if the database has records. If empty, seed from clean_graph.json.

    Returns True if seeding occurred, False if existing database was hydrated.
    """
    first_node = db.query(DBNode).first()
    if first_node is not None:
        # DB already populated; hydrate in-memory engine from SQLite
        hydrate_graph_from_db(engine, db)
        return False

    # First boot: search for candidate clean_graph.json
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
            hydrate_graph_from_db(engine, db)
            return True

    # If no seed file found, still initialize metrics on empty graph
    engine.compute_metrics()
    return False
