# Bug Report — AI-Powered Criminal Network Analysis System (SIH26189)

> **Scope:** Read-only audit. No code was modified. All findings are based on static inspection of `backend/`, `frontend/`, and `data/` as of 2026-09-04.
> **Method:** File-by-file review with `file_path:line_number` references for navigation.

---

## Table of Contents

1. [Backend — Entrypoint & Lifecycle](#1-backend--entrypoint--lifecycle)
2. [Backend — Graph Engine](#2-backend--graph-engine)
3. [Backend — Persistence & DB](#3-backend--persistence--db)
4. [Backend — NLP Ingestion](#4-backend--nlp-ingestion)
5. [Backend — Resolver / Deduplication](#5-backend--resolver--deduplication)
6. [Backend — API Layer](#6-backend--api-layer)
7. [Backend — Evidence & Dossier Services](#7-backend--evidence--dossier-services)
8. [Frontend — Core Page & State](#8-frontend--core-page--state)
9. [Frontend — Canvases](#9-frontend--canvases)
10. [Frontend — API Library & Data Mapping](#10-frontend--api-library--data-mapping)
11. [Frontend — Modals & Inspectors](#11-frontend--modals--inspectors)
12. [Frontend — Next.js API Route](#12-frontend--nextjs-api-route)
13. [Data Files & Contract Violations](#13-data-files--contract-violations)
14. [Config & Cross-Cutting](#14-config--cross-cutting)
15. [Summary Table](#15-summary-table)

---

## 1. Backend — Entrypoint & Lifecycle

### 1.1 Double hydration / triple `create_all`
- **File:** `backend/app/main.py:56-72` and `backend/app/services/graph_store.py:28-29`
- **Details:** `init_db_and_graph()` is called inside `lifespan()` (`main.py:58`) **and** synchronously inside `create_app()` (`main.py:72`). `graph_store.py:28` also calls `ensure_sample_loaded()` at **import time**. On `uvicorn --reload` this triggers 3× `Base.metadata.create_all` + `hydrate_graph_from_db`, causing race conditions, duplicate seed checks, and wasted DB I/O.
- **Impact:** High — intermittent `sqlite3.OperationalError: database is locked` and stale in-memory graph.

### 1.2 CORS misconfiguration
- **File:** `backend/app/main.py:64-70`
- **Details:** `allow_origins=["*"]` with `allow_credentials=True` violates the CORS spec. Browsers reject `Access-Control-Allow-Origin: *` when `Access-Control-Allow-Credentials: true`.
- **Impact:** High — frontend `fetch(..., credentials)` fails in production.

### 1.3 Duplicated seed-search logic
- **File:** `backend/app/main.py:39-44` vs `backend/app/services/persistence_service.py:245-250`
- **Details:** Two independent candidate lists (`parents[3]` is `D:\` on Windows) with no single source of truth. Divergence risks one path seeding while the other fails.
- **Impact:** Medium

### 1.4 Non-atomic ingest
- **File:** `backend/app/main.py:87-96`
- **Details:** `save_payload_to_db()` then `engine.add_node/edge` in two phases. If `engine.add_edge` auto-creates `Unknown` placeholder (`graph_engine.py:50-52`) but DB already flushed `Unknown` (`persistence_service.py:140-149`), types diverge until next `hydrate`.
- **Impact:** Medium — DB/graph inconsistency after ingest.

---

## 2. Backend — Graph Engine

### 2.1 Weighted undirected conversion discards keys
- **File:** `backend/app/core/graph_engine.py:101-108`
- **Details:** `for u, v in self.graph.edges():` ignores `key` (MultiDiGraph parallel edges). Weight increment is coincidentally correct due to implicit duplication, but `keys=True` is required for explicit correctness. `add_nodes_from` also drops node attributes.
- **Impact:** Low

### 2.2 Inconsistent membership test in filtered subgraph
- **File:** `backend/app/core/graph_engine.py:247-248`
- **Details:** `if node_id not in filtered and node_id in self.graph.nodes:` — `filtered` is a `Graph` (membership tests nodes), `self.graph.nodes` is a `NodeView`. Inconsistent View vs Graph semantics; `self.graph.nodes` should be `self.graph`.
- **Impact:** Low

### 2.3 Temporal metrics leakage
- **File:** `backend/app/core/graph_engine.py:284-310`
- **Details:** Lazy metrics check recomputes on **main** graph only, then backfills `betweenness/pagerank` from main into windowed `subgraph`. Metrics of the full graph leak into the temporal window, producing incorrect ranking in `get_filtered_subgraph`.
- **Impact:** High — forensic timeline analysis shows wrong kingpins.

### 2.4 Metadata pollution
- **File:** `backend/app/core/graph_engine.py:36-41`
- **Details:** `**node.metadata.model_dump()` without `exclude_none=True` injects `None` values into graph attributes. `persistence_service` correctly uses `exclude_none=True`.
- **Impact:** Low

### 2.5 Time parsing drift
- **File:** `backend/app/core/graph_engine.py:203,210,261`
- **Details:** `datetime.fromisoformat(text.replace("Z","+00:00"))` then `_as_naive` strips tz, `get_time_range` then `.isoformat()` drops tz. Frontend `new Date()` parse drifts by local offset.
- **Impact:** Medium

---

## 3. Backend — Persistence & DB

### 3.1 Idempotent edge key too narrow
- **File:** `backend/app/services/persistence_service.py:152-162`
- **Details:** Deduplication on `source+target+type+timestamp` only, ignoring `amount/tx_id/imei`. Two distinct `TRANSFERRED` transactions with same timestamp (e.g., `clean_graph.json:331-333` vs `341-347`) collapse into one.
- **Impact:** High — money-flow loss.

### 3.2 Type coercion fails on Indian number format
- **File:** `backend/app/services/persistence_service.py:126,131`
- **Details:** `float(raw_amt)` / `int(raw_dur)` fail on comma string `"1,50,000"`. `nlp.py:1177` strips commas but persistence does not.
- **Impact:** Medium

### 3.3 FK placeholder leak
- **File:** `backend/app/services/persistence_service.py:140-150` and `hydrate_graph_from_db:210`
- **Details:** Creates `Unknown` nodes without `evidence_source`. On hydrate, `type="Unknown"` masks true type if real node arrives later.
- **Impact:** Medium

### 3.4 `json.dumps(default=str)` breaks ISO-8601
- **File:** `backend/app/services/persistence_service.py:75,81,166,186`
- **Details:** Serialises `datetime` via `str()` not `isoformat()`, later `_parse_meta` loads as opaque string breaking `get_filtered_subgraph` timestamp parse.
- **Impact:** Low

### 3.5 Hard-coded SQLite path / thread safety
- **File:** `backend/app/db/session.py:14-19`
- **Details:** `DATABASE_URL = "sqlite:///./criminal_network.db"` relative to `cwd`, `check_same_thread=False` is marked safe but no pool/timeout handling for concurrent FastAPI workers.
- **Impact:** Medium

---

## 4. Backend — NLP Ingestion

### 4.1 Empty-ID MD5 collision
- **File:** `backend/app/core/nlp.py:122-134`
- **Details:** After `re.sub` an empty string becomes `md5(b"") == d41d8cd98f00b204e9800998ecf8427e` for **every** empty id. Multiple empty captures share the same node id.
- **Impact:** Medium

### 4.2 `now()` fallback poisons forensic timeline
- **File:** `backend/app/core/nlp.py:152-199`
- **Details:** `_normalize_timestamp` returns `datetime.now(timezone.utc).isoformat()` for `None/NaN/""`. Missing timestamps become *current wall time*, corrupting `TimelineSlider` and `get_filtered_subgraph`.
- **Impact:** High — should return `None` or raise.

### 4.3 Phone / bank-account regex cross-contamination
- **File:** `backend/app/core/nlp.py:57,64,559-590`
- **Details:** `PHONE_RE` `(?:\+|0{0,2})91[\s-]*` matches `0919876543210` inside `A/C 123456789012`. `BANK_RE` `\b\d{9,18}\b` then re-captures same 10-digit phones as accounts because `phone_set` filter happens after normalization with different string forms.
- **Impact:** High

### 4.4 Duplicate alias regex & span handling
- **File:** `backend/app/core/nlp.py:71-78,427-455`
- **Details:** `ALIAS_RE` and `ALIAS_INLINE_RE` are identical. Double loop creates duplicate aliases; `seen_alias_pairs` dedup is case-lower only, not span-based.
- **Impact:** Low

### 4.5 PS location over-filtering
- **File:** `backend/app/core/nlp.py:317-327,244`
- **Details:** `_extract_ps_locations` captures `PS Andheri` -> `["Andheri","Andheri"]`; global `_LOCATION_HINTS` then removes any suspect containing `andheri` even if valid name `Andheri Kumar`.
- **Impact:** Medium

### 4.6 Vehicle nodes silently dropped
- **File:** `backend/app/core/nlp.py:865-878`
- **Details:** Vehicles discarded as `Location` to stay schema-compliant, but `metadata.vehicles` merged only onto `suspects_in_fir[:1]` — if 0 suspects, vehicles lost.
- **Impact:** Low

### 4.7 CSV dialect sniff fragility
- **File:** `backend/app/core/nlp.py:924-929`
- **Details:** `Sniffer().sniff` on 4096 bytes with delimiters `,;\t|` may misdetect `|` on small header, causing `DictReader` mis-parse with no fallback to `,`.
- **Impact:** Medium

### 4.8 Transitive alias resolution cycle risk
- **File:** `backend/app/core/nlp.py:1431-1437`
- **Details:** `while cur in alias_id_map` with `visited` guard prevents infinite loop but if `A->B, B->A` cycle returns `A` arbitrarily.
- **Impact:** Low

### 4.9 Edge dedup signature includes `evidence_source`
- **File:** `backend/app/core/nlp.py:1241-1250`
- **Details:** Signature includes `evidence_source`, so two `OPERATES` edges with same `src/tgt/timestamp` but different `fir_no` are considered distinct, exploding edge count.
- **Impact:** Medium

---

## 5. Backend — Resolver / Deduplication

### 5.1 Shared global mutable maps
- **File:** `backend/app/core/resolver.py:39-44,229-231`
- **Details:** `ALIAS_LOOKUP`, `ALIAS_ID_MAP`, `_ALIAS_ID_MAP` point to **same dict object**; `clear()` on one clears others. External holder of `_ALIAS_ID_MAP` sees mutation. Not thread-safe across `parse_all_sources` calls.
- **Impact:** High — alias leakage between requests.

### 5.2 Threshold tuning causes over-merge
- **File:** `backend/app/core/resolver.py:333-381`
- **Details:** `PHONETIC_THRESHOLD=0.82` vs `FUZZY_THRESHOLD=0.88`. `share_first` exact lowers threshold to 0.82 even when first token is common `"Kumar"`, causing `Ramesh Kumar` <-> `Suresh Kumar` to merge. Guard `score<0.92` block is defeated by `overlap_bonus 0.10` + substring boost `0.88` (`resolver.py:158`) making score always >=0.88.
- **Impact:** High

### 5.3 Short-name bypass
- **File:** `backend/app/core/resolver.py:367-370`
- **Details:** `is_short_single_token <=6` skips fuzzy merge unless phonetic matches — `Bunty` (5) vs `Banty` share soundex `B530` and will incorrectly merge distinct nicknames.
- **Impact:** Medium

### 5.4 Canonical rank tie broken by label length
- **File:** `backend/app/core/resolver.py:396-405`
- **Details:** `is_primary` checks `normalized_alias_map.values()` which contains *both* alias and primary due to self-mapping (`resolver.py:255-256`), so every node gets `is_primary=1`. Tie broken by label length, not evidence quality.
- **Impact:** Medium

---

## 6. Backend — API Layer

### 6.1 `/api/graph` missing `stats`/`timeRange`
- **File:** `backend/app/api/graph.py:22-25`
- **Details:** Returns `get_cytoscape_elements()` directly. Frontend `lib/api.ts:219` expects `elements` but separately fetches `time-range`; contract is split without atomicity.
- **Impact:** Low

### 6.2 Evidence trail duplicate on bidirectional
- **File:** `backend/app/api/evidence.py:34-41`
- **Details:** For `bidirectional=True`, appends reverse pair without deduplication. If `CALLED` exists in both directions with same `edge_id`, duplicate appears.
- **Impact:** Low

### 6.3 Analytics recomputes on every request
- **File:** `backend/app/api/analytics.py:17`
- **Details:** `any("betweenness" not in ...)` O(n) scan on each request, no caching. Under load, `compute_metrics()` (betweenness O(n³)) runs repeatedly.
- **Impact:** Medium — perf.

### 6.4 Dossier no `Content-Length`
- **File:** `backend/app/api/dossier.py:18`
- **Details:** `build_dossier_pdf` returns raw bytes via `io.BytesIO` entirely in memory; large graphs (>200 edges) risk OOM, no `Content-Length` header.
- **Impact:** Low

---

## 7. Backend — Evidence & Dossier Services

### 7.1 Dropped metadata fields
- **File:** `backend/app/services/evidence_service.py:42-54`
- **Details:** Returns only `edge_id,type,timestamp,amount,duration,transaction_id,cdr_id,fir_id,fir_excerpt,source_document` — drops `evidence_source,tower_id,imei,fir_no` required by `verify_nlp.py` validations and `EvidenceModal:69-86`.
- **Impact:** Medium

### 7.2 Dossier sort key order wrong
- **File:** `backend/app/services/dossier_service.py:35-42`
- **Details:** Sort key `(_evidence_score, timestamp)` reversed — edges with higher timestamp dominate over evidence score because tuple order is `(score, timestamp)` string compare. `score=0, ts="2025-..."` ranks above `score=2, ts="2024-..."`.
- **Impact:** Medium

---

## 8. Frontend — Core Page & State

### 8.1 `Infinity` flash on load
- **File:** `frontend/app/page.tsx:65-90,114-152,189-193`
- **Details:** `currentTimestamp` initialized `""` then `useEffect` sets `maxDate`. Until resolved, `currentDateVal = Infinity`, `filteredEdges.filter(t <= Infinity)` returns all edges for one render frame (flash of unfiltered graph).
- **Impact:** Medium — UX flicker.

### 8.2 Hard-coded fallback timeline
- **File:** `frontend/app/page.tsx:132-135`
- **Details:** `minDate: '2024-03-15T00:00:00.000Z' / maxDate: '2025-01-25T23:59:59.000Z'` ignores actual `clean_graph.json` range `2024-03-15 — 2025-01-20`.
- **Impact:** Low

### 8.3 Ingest merge without dedup on source/target change
- **File:** `frontend/app/page.tsx:265-304`
- **Details:** `handleIngestSuccess` merges `newElements` locally without deduplication on `source/target` change; duplicate `node-ac-...` ids from CDR/LEDGER tabs collide with FIR accounts.
- **Impact:** Medium

---

## 9. Frontend — Canvases

### 9.1 ForceGraph fallback constants
- **File:** `frontend/components/canvas/ForceGraphCanvas.tsx:70-78,115-126`
- **Details:** `betweenness` fallback `0.45`, `riskScore/100` scale mix. `d3Force('charge').strength(-350)` and `link.distance(120)` re-applied in `useEffect:[graphData]` on every data change without `stop()`, causing physics jitter.
- **Impact:** Low

### 9.2 Duplicate interface definition
- **File:** `frontend/components/GraphCanvas.tsx:17-22` and `206-213`
- **Details:** `GraphCanvasProps` defined **twice** with incompatible signatures (first `selectedEdge` only, second adds `onSelectNode/selectedNode`). TypeScript merges them, `selectedNode` becomes optional unnoticed, static grid never receives selection in `page.tsx:523-534`.
- **Impact:** Medium — type safety broken.

### 9.3 Hard-coded coordinates / viewBox
- **File:** `frontend/components/GraphCanvas.tsx:24-55,312`
- **Details:** `SAMPLE_NODES` with `x,y` and `viewBox="0 0 960 520"` assume fixed container. Responsive resize not handled; foreign `width/height` ignored.
- **Impact:** Low

---

## 10. Frontend — API Library & Data Mapping

### 10.1 Risk score inflation
- **File:** `frontend/lib/api.ts:79-88,118`
- **Details:** `riskScore = Math.round(betweenness*60+pagerank*200+40)` clamped `Math.min(99, ...)`. For isolated nodes `betweenness=0` => `riskScore=40` artificially inflates low-risk nodes.
- **Impact:** Medium

### 10.2 Hard-coded `createdAt` fallback
- **File:** `frontend/lib/api.ts:113-118`
- **Details:** `createdAt` fallback `2024-03-15T00:00:00+00:00` not `+05:30` Indian time → timeline off by 5.5h; should use `+05:30` or UTC.
- **Impact:** Low

### 10.3 `FormData` flattening loses files
- **File:** `frontend/lib/api.ts:425-446`
- **Details:** `postIngestData` flattens `FormData` via `formObj[key]=val`, losing `File` binary. `toBackendGraphPayload` maps `val` as string `"[object File]"` → Pydantic creates node with label `"[object File]"`.
- **Impact:** High — file upload broken.

### 10.4 `AbortController` timeout leak
- **File:** `frontend/lib/api.ts:205-213,280-295`
- **Details:** `setTimeout(() => controller.abort(), 3500)` but `clearTimeout` not called on `fetch` throw path → leak.
- **Impact:** Low

### 10.5 Pydantic enum mismatch
- **File:** `frontend/lib/api.ts:402-420` and `backend/app/models/graph_models.py:14-15`
- **Details:** Frontend maps any case to `'Suspect'|'Phone'|...` via `toUpperCase().includes`, but backend `Literal["Suspect",...]` is case-sensitive. Ingest payload with `type:"SUSPECT"` uppercase passes frontend but Pydantic rejects.
- **Impact:** Medium

---

## 11. Frontend — Modals & Inspectors

### 11.1 Account regex too narrow
- **File:** `frontend/components/modals/IngestionModal.tsx:169-173`
- **Details:** `acRegex = /(?:A\/C\s*#?|account\s*#?)?(\d{11,16})/gi` captures only 11-16 digits but backend spec is `9-18`. 9-10 digit accounts (e.g., `9876543210` phone) mis-classified.
- **Impact:** Medium

### 11.2 Colliding edge ids via `Date.now()`
- **File:** `frontend/components/modals/IngestionModal.tsx:300,366`
- **Details:** `edge-cdr-${idx}-${Date.now()}` not monotonic across tabs; rapid ingest creates colliding ids.
- **Impact:** Low

### 11.3 EvidenceModal key typos
- **File:** `frontend/components/modals/EvidenceModal.tsx:68-86`
- **Details:** Maps `metadata.imei` vs `metadata.tower_id` but backend CDR emits `tower_id`/`imei` lower; fallback `metadata.cellTower` typo never matches. `metadata.utr` fallback for `transactionId` but backend uses `tx_id`.
- **Impact:** Medium — missing tower/txn display.

### 11.4 RiskLeaderboard filter hides brokers
- **File:** `frontend/components/intelligence/RiskLeaderboard.tsx:19-23` and `RiskLeaderboard:96-104`
- **Details:** Filter `type.includes('SUSPECT') || riskScore>=80` hides high-betweenness `Phone` brokers (e.g., burner SIM `betweenness=0.72` but `riskScore=45` in `ForceGraphCanvas:88`). Leaderboard incomplete.
- **Impact:** Medium

---

## 12. Frontend — Next.js API Route

### 12.1 Fragile path resolution
- **File:** `frontend/app/api/graph/route.ts:8-15`
- **Details:** `rootPath = join(process.cwd(), '../data/clean_graph.json')` assumes `frontend/` cwd. In Vercel production `process.cwd() = /app`, resolves to `/data` not found; fallback `localPath = ./data/clean_graph.json` also missing (data at repo root). Returns 500.
- **Impact:** High — production fallback broken.

### 12.2 Missing schema validation
- **File:** `frontend/app/api/graph/route.ts:22-50`
- **Details:** No validation of `rawNodes/rawEdges` shape; malformed JSON exposes raw metadata without sanitization. `betweenness` fallback `0.84` hardcoded for Suspects.
- **Impact:** Medium

---

## 13. Data Files & Contract Violations

### 13.1 Sample graph violates strict schema
- **File:** `data/sample_graph.json:28-47`
- **Details:** `Phone` labels `"+91-98XXXXXX01"` fail `PHONE_LABEL_RE ^[6-9]\d{9}$`, `Account` labels `"XXXX-XXXX-1101"` fail `ACCOUNT_LABEL_RE ^\d{9,18}$`. `verify_nlp.py:317-375` would reject sample graph as invalid.
- **Impact:** Medium

### 13.2 `CO_ACCUSED_IN` to `CrimeCase`
- **File:** `data/sample_graph.json:241-320` and `data/clean_graph.json` edges
- **Details:** `CO_ACCUSED_IN` edges target `case_fir42` (`CrimeCase`) violate `verify_nlp.py:409-412` (`CO_ACCUSED_IN` must be `Suspect->Suspect`). Should be `OPERATES`.
- **Impact:** Medium — validation failure.

### 13.3 Inverted `so_relations`
- **File:** `data/clean_graph.json:121-126`
- **Details:** `so_relations: [{"child":"Rama","father":"Suresh Kumar"}]` inverted — child should be `Ramesh Kumar`, father `Suresh Kumar`; indicates `nlp.py:491-503` alias correction partially failed.
- **Impact:** Low

### 13.4 CSV amount commas
- **File:** `data/bank_transactions.csv` (referenced, not inspected) and `backend/app/core/nlp.py:1177`
- **Details:** Amounts with Indian commas `"1,00,000"` parsed via `float(str.replace(",",""))` in NLP but not in `persistence_service:126`.
- **Impact:** Medium

---

## 14. Config & Cross-Cutting

### 14.1 Type enum drift
- **File:** `frontend/types/graph.ts:1-16` vs `backend/app/models/graph_models.py:14-15`
- **Details:** Frontend `NodeType = 'SUSPECT'|'BANK_ACCOUNT'|...|string` allows any literal; backend `Literal["Suspect",...]` is strict. `string` fallback bypasses frontend type safety, causing silent ingest rejections.
- **Impact:** Medium

### 14.2 Missing health check
- **File:** `backend/app/api/health.py` (not inspected in depth, but `main.py:74` includes router)
- **Details:** Health endpoint likely returns 200 without DB/graph check, masking hydration failures.
- **Impact:** Low

### 14.3 No pagination / rate limiting
- **File:** `backend/app/main.py` (all routers)
- **Details:** `GET /api/graph` returns entire graph (13 nodes/17 edges in clean, but scales to 1000+). No pagination, compression, or rate limiting.
- **Impact:** Medium — perf at scale.

---

## 15. Summary Table

| # | Component | Severity | File:Line | Bug |
|---|-----------|----------|-----------|-----|
| 1 | Backend | High | `main.py:56-72`, `graph_store.py:28` | Triple hydration / double `create_all` |
| 2 | Backend | High | `main.py:64-70` | CORS `*` + `credentials` |
| 3 | Backend | High | `graph_engine.py:284-310` | Temporal metrics leakage |
| 4 | Backend | High | `persistence_service.py:152-162` | Edge dedup ignores amount/tx_id |
| 5 | Backend | High | `nlp.py:152-199` | `now()` fallback poisons timeline |
| 6 | Backend | High | `nlp.py:57,64` | Phone/bank regex cross-contamination |
| 7 | Backend | High | `resolver.py:39-44` | Shared global mutable maps |
| 8 | Backend | High | `resolver.py:333-381` | Over-merge threshold |
| 9 | Frontend | High | `lib/api.ts:425-446` | `FormData` file upload broken |
| 10 | Frontend | High | `app/api/graph/route.ts:8-15` | Fragile `process.cwd()` path |
| 11 | Backend | Medium | `persistence_service.py:126,131` | Comma amount parse |
| 12 | Backend | Medium | `graph_engine.py:203,210,261` | Time drift |
| 13 | Backend | Medium | `nlp.py:122-134` | Empty-ID MD5 collision |
| 14 | Backend | Medium | `nlp.py:317-327` | PS over-filter |
| 15 | Backend | Medium | `resolver.py:367-370` | Short-name soundex merge |
| 16 | Backend | Medium | `services/evidence_service.py:42-54` | Dropped metadata |
| 17 | Backend | Medium | `services/dossier_service.py:35-42` | Sort key order |
| 18 | Frontend | Medium | `app/page.tsx:189-193` | `Infinity` flash |
| 19 | Frontend | Medium | `components/GraphCanvas.tsx:17,206` | Duplicate Props interface |
| 20 | Frontend | Medium | `lib/api.ts:79-88` | Risk score inflation |
| 21 | Frontend | Medium | `lib/api.ts:402-420` | Enum case mismatch |
| 22 | Frontend | Medium | `modals/IngestionModal.tsx:169-173` | Account regex 11-16 |
| 23 | Frontend | Medium | `intelligence/RiskLeaderboard.tsx:19-23` | Hides phone brokers |
| 24 | Data | Medium | `sample_graph.json:28-47,241-320` | Schema violations |
| 25 | Config | Medium | `types/graph.ts:1-16` vs `graph_models.py:14` | Type enum drift |

---

*Generated without modifying any source file, as requested.*
