# HyperSearch

[![CI](https://github.com/vanditsramblings/StaticSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/vanditsramblings/StaticSearch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/hypersearch.svg)](https://pypi.org/project/hypersearch/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Hyper-fast semantic search over static data.** Embedded DuckDB + local embeddings. Zero external infrastructure.

HyperSearch is designed for **static or slow-mutating datasets** where you want lightning-fast vector similarity search without the complexity of distributed vector databases. Bring your own CSV, JSON, or Parquet data — HyperSearch handles embedding, indexing, and querying locally.

---

## Highlights

- ⚡ **Sub-20ms search** — HNSW indexes on DuckDB, tuned for speed
- 📦 **Zero infrastructure** — No servers, no cloud, no API keys. One `pip install`.
- 🔄 **Full lifecycle** — Ingest → Embed → Search → Re-embed → Snapshot/Restore
- 🎯 **Rich filters** — Combine vector similarity with SQL metadata filters
- 🔒 **Atomic ingestion** — Zero-downtime hot-reload via staging + pointer swap
- 🐍 **Python library + CLI + REST API** — Use however fits your workflow
- 📊 **Built-in benchmarking** — Latency percentiles (p50/p95/p99) under load

---

## Installation

```bash
pip install hypersearch
```

Or install from source:

```bash
git clone https://github.com/vanditsramblings/StaticSearch.git && cd StaticSearch
pip install -e ".[dev]"
```

> **Note:** The first import downloads the `all-MiniLM-L6-v2` embedding model (~80MB). Subsequent imports use the cached model.

---

## Quick Start — Python Library

```python
from hypersearch import HyperSearch

# Initialize (creates data directory automatically)
with HyperSearch(data_dir="./my_data") as hs:
    # Create a collection
    col = hs.create_collection("products")

    # Ingest a CSV file
    col.ingest(
        "products.csv",
        template="{name} {description}",
        metadata_columns=["name", "category", "price"],
    )

    # Search
    results = col.search("waterproof hiking boots", top_k=5)
    for hit in results:
        print(f"  {hit.score:.4f}  {hit.document[:80]}")
        print(f"           {hit.metadata}")

    # Search with filters
    results = col.search(
        "lightweight running shoes",
        top_k=10,
        filters={"category": "footwear", "price": {"$lt": 100}},
    )

    # Export results to a DataFrame
    df = results.to_dataframe()
```

---

## Quick Start — CLI

```bash
# Start the API server
hypersearch serve

# Ingest a dataset
hypersearch ingest my_collection data.csv \
  --template "{title} {description}" \
  --metadata "title,category,price"

# Search from the terminal
hypersearch search my_collection "waterproof hiking boots" --top-k 5

# Backup a collection
hypersearch snapshot my_collection

# Restore from backup
hypersearch restore my_collection snapshots/my_collection_20240101T120000Z.duckdb
```

---

## Quick Start — REST API

```bash
# Start the server
hypersearch serve --host 0.0.0.0 --port 8000

# Create a collection
curl -X POST http://127.0.0.1:8000/v1/collections \
  -H "Content-Type: application/json" \
  -d '{"name": "vulnerabilities"}'

# Ingest a CSV dataset
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/ingest \
  -F "file=@data.csv" \
  -F 'config={"search_template": "{cve} {description}", "metadata_columns": ["cve", "cvss_score", "severity"]}'

# Search
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/search \
  -H "Content-Type: application/json" \
  -d '{"query": "remote code execution buffer overflow", "top_k": 5}'

# Search with metadata filters
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/search \
  -H "Content-Type: application/json" \
  -d '{"query": "SQL injection", "top_k": 10, "filters": {"severity": "CRITICAL"}}'
```

Full Swagger docs at **http://127.0.0.1:8000/docs** when the server is running.

---

## Library API Reference

### `HyperSearch`

The main client. Manages collections and configuration.

```python
from hypersearch import HyperSearch

hs = HyperSearch(
    data_dir="./data",              # Where .duckdb files are stored
    snapshot_dir="./snapshots",     # Where backups go
    model="all-MiniLM-L6-v2",      # Any sentence-transformers model
    device="cpu",                   # "cpu" or "cuda"
    batch_size=256,                 # Rows per embedding batch
    hnsw_m=48,                      # HNSW max edges per node
    hnsw_ef_construction=256,       # Build-time beam width
    hnsw_ef_search=128,             # Query-time beam width
)
```

| Method | Returns | Description |
|--------|---------|-------------|
| `create_collection(name)` | `Collection` | Create a new collection |
| `get_collection(name)` | `Collection` | Open an existing collection |
| `list_collections()` | `list[dict]` | List all collections with metadata |
| `delete_collection(name)` | `None` | Delete a collection and its data |
| `close()` | `None` | Close all connections |

Supports the context manager protocol (`with HyperSearch(...) as hs:`).

### `Collection`

Per-collection operations: ingest, search, reindex, snapshot, restore.

| Method | Returns | Description |
|--------|---------|-------------|
| `ingest(source, *, template, metadata_columns, batch_size)` | `int` | Ingest a CSV/JSON/Parquet file |
| `search(query, *, top_k, filters)` | `SearchResults` | Semantic search with optional filters |
| `reindex(*, template, batch_size)` | `int` | Re-embed all documents |
| `snapshot(dest_dir)` | `Path` | Create a backup snapshot |
| `restore(snapshot_path)` | `None` | Restore from a snapshot |
| `row_count` | `int` | Number of rows (property) |
| `info` | `dict` | Collection metadata (property) |

### `SearchResults` / `SearchHit`

Ergonomic result objects with iteration, indexing, and DataFrame conversion.

```python
results = col.search("remote code execution", top_k=5)

# Iterate
for hit in results:
    print(hit.score, hit.document, hit.metadata)

# Index
best = results[0]

# Length
print(f"Found {len(results)} results in {results.latency_ms:.1f}ms")

# Convert to Polars DataFrame
df = results.to_dataframe()

# Convert to plain dicts
dicts = results.to_dicts()
```

---

## Full Lifecycle

### 1. Ingest

```python
col = hs.create_collection("products")
rows = col.ingest(
    "products.csv",
    template="{name} {description}",       # How to build the search document
    metadata_columns=["name", "price"],    # Columns to store as filterable metadata
    batch_size=512,                        # Embedding batch size
)
print(f"Ingested {rows:,} rows")
```

Supports **CSV**, **JSON**, **JSONL/NDJSON**, and **Parquet** files. Schema inference is automatic.

### 2. Search

```python
results = col.search("waterproof boots", top_k=10)
for hit in results:
    print(f"  [{hit.score:.4f}] {hit.document[:80]}")
```

### 3. Search with Filters

```python
results = col.search(
    "hiking gear",
    top_k=10,
    filters={
        "category": "outdoor",                    # Equality
        "price": {"$lt": 100},                    # Less than
        "brand": {"$in": ["Nike", "Adidas"]},     # In set
        "rating": {"$between": [4.0, 5.0]},       # Range
    },
)
```

### 4. Re-embed (Reindex)

```python
# Re-embed with a new template
col.reindex(template="{name} {category} {description}")
```

### 5. Snapshot & Restore

```python
# Backup
snap_path = col.snapshot()
print(f"Backed up to {snap_path}")

# Restore
col.restore(snap_path)
```

---

## Search Filter Operators

| Operator | SQL Equivalent | Example |
|----------|---------------|---------|
| `value` | `= value` | `"severity": "HIGH"` |
| `$eq` | `= value` | `{"$eq": "HIGH"}` |
| `$neq` | `!= value` | `{"$neq": "LOW"}` |
| `$gt` | `> value` | `{"$gt": 7.0}` |
| `$gte` | `>= value` | `{"$gte": 7.0}` |
| `$lt` | `< value` | `{"$lt": 5.0}` |
| `$lte` | `<= value` | `{"$lte": 5.0}` |
| `$in` | `IN (...)` | `{"$in": ["a", "b"]}` |
| `$between` | `BETWEEN a AND b` | `{"$between": [1, 10]}` |

---

## Configuration

### YAML Configuration

Copy `hypersearch.example.yaml` to `hypersearch.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  workers: 1
  api_key: null                     # Set a string to enable X-API-Key auth

storage:
  data_dir: "./data"
  snapshot_dir: "./snapshots"

embedding:
  model: "all-MiniLM-L6-v2"        # Any sentence-transformers model
  batch_size: 256
  device: "cpu"                     # "cpu" or "cuda"
  dimensions: 384
  hnsw_m: 48                        # Higher = better recall, slower build
  hnsw_ef_construction: 256         # Higher = better index quality
  hnsw_ef_search: 128               # Higher = better recall at query time
  query_cache_size: 2048            # LRU cache for query embeddings

defaults:
  search_template: "{text}"
  metadata_columns: []
  top_k: 10
```

### Environment Variable Overrides

Any config value can be overridden via environment variables with the `HYPER_` prefix:

```bash
HYPER_SERVER__PORT=9000 HYPER_SERVER__API_KEY=secret123 hypersearch serve
```

---

## CLI Reference

```bash
# Server
hypersearch serve --host 0.0.0.0 --port 8000 --workers 1

# Ingest
hypersearch ingest <collection> <file> \
  --template "{title} {description}" \
  --metadata "title,category,price" \
  --batch-size 512

# Search
hypersearch search <collection> "query text" \
  --top-k 5 \
  --filters '{"severity": "HIGH"}'

# Snapshot
hypersearch snapshot <collection>

# Restore
hypersearch restore <collection> <snapshot_path>

# All commands support --config to specify a custom YAML file
hypersearch serve --config /path/to/hypersearch.yaml
```

---

## REST API Reference

All endpoints are documented at **http://127.0.0.1:8000/docs** (Swagger UI) or **http://127.0.0.1:8000/redoc** (ReDoc).

### Collections

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/collections` | Create a new collection |
| `GET` | `/v1/collections` | List all collections |
| `DELETE` | `/v1/collections/{name}` | Delete a collection |

### Data Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/collections/{name}/ingest` | Upload and index a dataset |
| `POST` | `/v1/collections/{name}/search` | Semantic search with filters |
| `POST` | `/v1/collections/{name}/snapshot` | Create a backup snapshot |
| `POST` | `/v1/collections/{name}/restore` | Restore from a snapshot |

### Operational

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | System health (RAM, disk, uptime) |
| `GET` | `/metrics` | Prometheus-format metrics |

---

## Benchmarks

See [BENCHMARKS.md](BENCHMARKS.md) for detailed performance data with system configuration.

### Summary (CVE Dataset, CPU, `all-MiniLM-L6-v2`)

| Dataset | Ingest Time | Search p50 | Search p95 | Steady RAM | DB Size |
|---------|-------------|------------|------------|------------|---------|
| 10,000 rows | ~108s | ~20ms | ~21ms | ~870 MB | 36 MB |
| 50,000 rows | ~540s | ~25ms | ~28ms | ~950 MB | 177 MB |

> Search latencies include query embedding + HNSW traversal. Cached queries (typeahead) run in ~10-18ms.

### Running Benchmarks

```bash
# Run tiered benchmarks (10k + 50k)
python -m benchmarks.pipeline --tiers 10000,50000 --output-md BENCHMARKS.md

# HTTP-based benchmark (requires running server)
python -m benchmarks.run --collection bench_cve --concurrency 1,5,10
```

---

## Demo Datasets

### CVE Summary (Recommended)

Pre-enriched CVE records with descriptions and CVSS scores. ~50k rows, fast download.

```bash
python -m datasets.fetch_cve_summary --limit 50000
# Output: datasets/output/cve_summary.csv
```

### NVD Full Dataset

Complete NVD vulnerability data with technology detection. ~700k rows.

```bash
python -m datasets.fetch_nvd_full
# Output: datasets/output/nvd_full.csv
```

### Bootstrap Script

One-command setup for development:

```bash
./bootstrap.sh             # Full setup
./bootstrap.sh --install   # Install Python dependencies
./bootstrap.sh --dataset   # Download CVE demo dataset
./bootstrap.sh --ingest    # Ingest into HyperSearch
./bootstrap.sh --serve     # Start API server
```

---

## Architecture

### Data Flow

```
User Data (CSV/JSON/Parquet)
    │
    ▼
┌─────────────────────┐
│   Polars Reader      │  ← Chunked streaming (memory-safe)
│   Schema Inference   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Document Builder    │  ← str.format_map("{title} {desc}")
│  _hyper_document_    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  EmbeddingManager    │  ← all-MiniLM-L6-v2 (384 dims)
│  _hyper_vector_      │     Batched, lazy-loaded, LRU-cached
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Staging DuckDB      │  ← {name}.staging.duckdb
│  HNSW Index Build    │     M=48, ef_construction=256
└─────────┬───────────┘
          │
          ▼ os.replace() (atomic)
┌─────────────────────┐
│  Live DuckDB         │  ← {name}.duckdb
│  Serving Queries     │     Zero-downtime swap
└─────────────────────┘
```

### Project Structure

```
StaticSearch/
├── hypersearch/                # Core package (pip install hypersearch)
│   ├── __init__.py             # Public API: HyperSearch, Collection, SearchResults
│   ├── client.py               # HyperSearch top-level client
│   ├── collection.py           # Collection class (ingest/search/reindex)
│   ├── results.py              # SearchHit, SearchResults dataclasses
│   ├── config.py               # YAML + env config loader
│   ├── models.py               # Pydantic request/response schemas
│   ├── db.py                   # DuckDB connection lifecycle
│   ├── engine.py               # Vector search execution
│   ├── ingest.py               # Data processing pipeline
│   ├── embeddings.py           # Sentence-transformer embedding manager
│   ├── hot_reload.py           # Atomic staging + pointer swap
│   ├── snapshot.py             # Backup/restore operations
│   ├── metrics.py              # Latency histograms + system stats
│   ├── middleware.py           # API-key authentication
│   ├── server.py               # FastAPI app factory
│   ├── cli.py                  # Typer CLI
│   └── routes/                 # REST API endpoint routers
│
├── tests/                      # Comprehensive test suite
├── benchmarks/                 # Performance harness
├── datasets/                   # Dataset acquisition scripts
├── .github/workflows/          # CI/CD (test + PyPI release)
├── pyproject.toml              # Build system + dependency manifest
├── BENCHMARKS.md               # Performance results
└── README.md                   # This file
```

---

## Development

### Setup

```bash
git clone https://github.com/vanditsramblings/StaticSearch.git
cd StaticSearch
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
# Fast tests only
python -m pytest tests/ -v -m "not slow"

# Full suite
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ -v --tb=short
```

### Linting

```bash
ruff check hypersearch/ tests/
ruff format hypersearch/ tests/
```

### Building

```bash
python -m build
pip install dist/hypersearch-*.whl
```

---

## OpenAPI Specification

Export the auto-generated OpenAPI spec:

```bash
python scripts/export_openapi.py
python scripts/export_openapi.py --format yaml --output openapi.yaml
```

The spec is also available live at `http://127.0.0.1:8000/openapi.json` when the server is running.

---

## License

MIT
