"""Comprehensive end-to-end backend test script for Chitragupta SIH-Proto."""

from __future__ import annotations
import sys
import os
import json
import traceback
from pathlib import Path

# Add backend to sys.path
backend_dir = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

def run_tests():
    print("==================================================")
    print("STARTING COMPREHENSIVE BACKEND TEST SUITE")
    print("==================================================")

    from app.main import app, init_db_and_graph
    from app.services.graph_store import engine, get_engine
    from app.db.session import SessionLocal, engine as db_engine
    from app.db.models import DBNode, DBEdge
    from app.core.nlp import parse_all_sources
    from app.core.resolver import resolve_suspect_aliases

    client = TestClient(app)
    failures = []
    passes = 0

    def assert_test(condition: bool, test_name: str, extra_info: str = ""):
        nonlocal passes
        if condition:
            passes += 1
            print(f"[PASS] {test_name}")
        else:
            print(f"[FAIL] {test_name}: {extra_info}")
            failures.append(f"{test_name}: {extra_info}")

    # 1. Health Check
    try:
        res = client.get("/health")
        assert_test(res.status_code == 200 and res.json().get("status") == "ok", "GET /health status 200 ok", f"got {res.status_code}, {res.text}")
    except Exception as e:
        assert_test(False, "GET /health exception", str(e))

    # 2. Database & Hydration Check
    try:
        with SessionLocal() as db:
            node_count = db.query(DBNode).count()
            edge_count = db.query(DBEdge).count()
            assert_test(node_count > 0, "DBNode count > 0", f"node_count={node_count}")
            assert_test(edge_count > 0, "DBEdge count > 0", f"edge_count={edge_count}")
            print(f"       Total DB Nodes: {node_count}, Total DB Edges: {edge_count}")
    except Exception as e:
        assert_test(False, "Database queries", str(e))

    # 3. GET /api/graph
    try:
        res = client.get("/api/graph")
        assert_test(res.status_code == 200, "GET /api/graph status 200", f"got {res.status_code}")
        data = res.json()
        elements = data.get("elements", {})
        nodes = elements.get("nodes", [])
        edges = elements.get("edges", [])
        assert_test(len(nodes) > 0, "GET /api/graph returns nodes", f"got {len(nodes)} nodes")
        assert_test(len(edges) > 0, "GET /api/graph returns edges", f"got {len(edges)} edges")

        # Verify node data attributes
        first_node = nodes[0]["data"]
        assert_test("id" in first_node and "label" in first_node and "type" in first_node, 
                    "Node has id, label, type", str(first_node))
        assert_test("betweenness" in first_node and "pagerank" in first_node,
                    "Node has betweenness and pagerank", str(first_node))

        # Verify edge data attributes
        first_edge = edges[0]["data"]
        assert_test("source" in first_edge and "target" in first_edge and "type" in first_edge,
                    "Edge has source, target, type", str(first_edge))
    except Exception as e:
        assert_test(False, "GET /api/graph exception", str(e))

    # 4. GET /api/graph/time-range
    time_range = {}
    try:
        res = client.get("/api/graph/time-range")
        assert_test(res.status_code == 200, "GET /api/graph/time-range status 200", f"got {res.status_code}")
        time_range = res.json()
        assert_test("earliest" in time_range and "latest" in time_range, "time-range keys present", str(time_range))
        print(f"       Earliest: {time_range.get('earliest')}, Latest: {time_range.get('latest')}")
    except Exception as e:
        assert_test(False, "GET /api/graph/time-range exception", str(e))

    # 5. GET /api/graph with time filtering
    try:
        earliest = time_range.get("earliest")
        latest = time_range.get("latest")
        if earliest and latest:
            res = client.get(f"/api/graph?start_time={earliest}&end_time={latest}")
            assert_test(res.status_code == 200, "GET /api/graph with valid time range status 200", f"got {res.status_code}")
            sub_nodes = res.json().get("elements", {}).get("nodes", [])
            assert_test(len(sub_nodes) > 0, "Filtered graph returns nodes", f"count={len(sub_nodes)}")
            
            # Invalid ISO format should return 400
            res_bad = client.get("/api/graph?start_time=invalid-time&end_time=invalid-time")
            assert_test(res_bad.status_code == 400, "Invalid start_time returns 400", f"got {res_bad.status_code}")
    except Exception as e:
        assert_test(False, "GET /api/graph temporal filter exception", str(e))

    # 6. GET /api/analytics/high-risk
    try:
        res = client.get("/api/analytics/high-risk?limit=5")
        assert_test(res.status_code == 200, "GET /api/analytics/high-risk status 200", f"got {res.status_code}")
        results = res.json().get("results", [])
        assert_test(len(results) > 0, "High-risk returns results", f"got {len(results)} items")
        if results:
            first_hr = results[0]
            assert_test("node_id" in first_hr and "betweenness" in first_hr and "rank" in first_hr,
                        "High-risk entry schema valid", str(first_hr))
            print(f"       Top High-Risk Suspect: {first_hr.get('label')} ({first_hr.get('node_id')}) - Betweenness: {first_hr.get('betweenness')}")
    except Exception as e:
        assert_test(False, "GET /api/analytics/high-risk exception", str(e))

    # 7. GET /api/evidence-trail
    try:
        # Find an edge in graph to test evidence trail
        res_graph = client.get("/api/graph")
        edges = res_graph.json().get("elements", {}).get("edges", [])
        if edges:
            sample_edge = edges[0]["data"]
            src = sample_edge["source"]
            tgt = sample_edge["target"]
            res_ev = client.get(f"/api/evidence-trail?source={src}&target={tgt}")
            assert_test(res_ev.status_code == 200, f"GET /api/evidence-trail for {src} -> {tgt} status 200", f"got {res_ev.status_code}")
            ev_data = res_ev.json()
            assert_test("relationships" in ev_data, "Evidence trail has relationships", str(ev_data))
            assert_test(len(ev_data.get("relationships", [])) > 0, "Evidence trail non-empty", f"count={len(ev_data.get('relationships', []))}")

        # Non-existent edge should return 404
        res_non = client.get("/api/evidence-trail?source=non_existent_1&target=non_existent_2")
        assert_test(res_non.status_code == 404, "Evidence trail for unknown nodes returns 404", f"got {res_non.status_code}")
    except Exception as e:
        assert_test(False, "GET /api/evidence-trail exception", str(e))

    # 8. GET /api/export-dossier
    try:
        res_dossier = client.get("/api/export-dossier")
        assert_test(res_dossier.status_code == 200, "GET /api/export-dossier status 200", f"got {res_dossier.status_code}")
        content = res_dossier.content
        assert_test(content.startswith(b"%PDF"), "Dossier content starts with %PDF", f"starts with {content[:10]!r}")
        assert_test(len(content) > 1000, "Dossier PDF has substantial size", f"size={len(content)} bytes")
        print(f"       Generated PDF size: {len(content)} bytes")
    except Exception as e:
        assert_test(False, "GET /api/export-dossier exception", str(e))

    # 9. POST /api/ingest
    try:
        test_payload = {
            "nodes": [
                {
                    "id": "suspect_test_runner_e2e",
                    "label": "Test Operative Alpha",
                    "type": "Suspect",
                    "metadata": {"role": "Tester", "location": "Test Lab"}
                },
                {
                    "id": "phone_9998887776",
                    "label": "+91 9998887776",
                    "type": "Phone",
                    "metadata": {"imei": "123456789012345"}
                }
            ],
            "edges": [
                {
                    "source": "suspect_test_runner_e2e",
                    "target": "phone_9998887776",
                    "type": "OPERATES",
                    "metadata": {"evidence_source": "TEST_CASE_001", "timestamp": "2024-03-20T10:00:00Z"}
                }
            ]
        }
        res_ingest = client.post("/api/ingest", json=test_payload)
        assert_test(res_ingest.status_code == 200, "POST /api/ingest status 200", f"got {res_ingest.status_code}, {res_ingest.text}")
        ingest_data = res_ingest.json()
        assert_test(ingest_data.get("status") == "loaded", "Ingest status is 'loaded'", str(ingest_data))

        # Verify ingested node exists in subsequent graph call
        res_graph_after = client.get("/api/graph")
        node_ids = [n["data"]["id"] for n in res_graph_after.json()["elements"]["nodes"]]
        assert_test("suspect_test_runner_e2e" in node_ids, "Ingested node reflected in GET /api/graph", "not found")

        # Verify ingested node persisted in SQLite
        with SessionLocal() as db:
            persisted = db.query(DBNode).filter(DBNode.id == "suspect_test_runner_e2e").first()
            assert_test(persisted is not None, "Ingested node persisted in SQLite DBNode", "not found in DB")
            if persisted:
                assert_test(persisted.label == "Test Operative Alpha", "DBNode label matches", persisted.label)
    except Exception as e:
        assert_test(False, "POST /api/ingest exception", str(e))

    # 10. Test NLP parse_all_sources
    try:
        data_dir = Path(__file__).resolve().parent / "data"
        if data_dir.exists():
            payload = parse_all_sources(str(data_dir))
            # Verify structure
            has_nodes = hasattr(payload, "nodes") or "nodes" in payload
            has_edges = hasattr(payload, "edges") or "edges" in payload
            assert_test(has_nodes and has_edges, "parse_all_sources returns nodes and edges", str(type(payload)))
            nodes_val = payload.nodes if hasattr(payload, "nodes") else payload["nodes"]
            edges_val = payload.edges if hasattr(payload, "edges") else payload["edges"]
            assert_test(len(nodes_val) > 0, "parse_all_sources parsed nodes", f"count={len(nodes_val)}")
            assert_test(len(edges_val) > 0, "parse_all_sources parsed edges", f"count={len(edges_val)}")
            print(f"       NLP Parsed Nodes: {len(nodes_val)}, Parsed Edges: {len(edges_val)}")
    except Exception as e:
        assert_test(False, "NLP parse_all_sources exception", f"{e}\n{traceback.format_exc()}")

    # 11. Test Entity Resolver
    try:
        sample_nodes = [
            {"id": "s1", "label": "Ramesh Kumar", "type": "Suspect", "metadata": {}},
            {"id": "s2", "label": "Ramesh", "type": "Suspect", "metadata": {}},
            {"id": "s3", "label": "Bunty", "type": "Suspect", "metadata": {}},
        ]
        alias_map = {"Bunty": "Ramesh Kumar"}
        resolved = resolve_suspect_aliases(sample_nodes, alias_map)
        assert_test(isinstance(resolved, list) and len(resolved) <= len(sample_nodes), 
                    "resolve_suspect_aliases returns deduplicated node list", f"count={len(resolved)}")
        print(f"       Resolved count: {len(resolved)} (from {len(sample_nodes)})")
    except Exception as e:
        assert_test(False, "Entity resolver exception", f"{e}\n{traceback.format_exc()}")

    print("\n==================================================")
    print(f"TEST RUN COMPLETE: {passes} PASSED, {len(failures)} FAILED")
    print("==================================================")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
    return len(failures) == 0

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
