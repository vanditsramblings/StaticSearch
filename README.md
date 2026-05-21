# HyperSearch

**BYOD semantic search engine** — embedded DuckDB + local embeddings. Zero external infrastructure required.

HyperSearch is designed for **static or slow-mutating datasets** where you want lightning-fast vector similarity search without the complexity of distributed vector databases. Bring your own CSV, JSON, or Parquet data — HyperSearch handles embedding, indexing, and querying locally.

---

## Features

- **Embedded vector storage** — DuckDB with HNSW indexes, one file per collection
- **Local ML pipeline** — `sentence-transformers` embeddings, no API keys or cloud services
- **BYOD flexibility** — Ingest CSV, JSON, or Parquet with automatic schema inference
- **Rich search filters** — Combine vector similarity with SQL metadata filters (`$gt`, `$lt`, `$in`, `$between`)
- **Zero-downtime ingestion** — Atomic hot-reload via staging database + pointer swap
- **Snapshot/restore** — One-command backup and recovery
- **Built-in benchmarking** — Latency percentiles (p50/p95/p99) under concurrent load
- **OpenAPI spec** — Auto-generated, contract-driven API documentation

## Quick Start

```bash
# Clone and bootstrap (installs deps, downloads CVE dataset, starts server)
git clone <repo-url> && cd StaticSearch
./bootstrap.sh

# Or step-by-step:
./bootstrap.sh --install     # Install Python dependencies
./bootstrap.sh --dataset     # Download CVE/EPSS demo dataset
./bootstrap.sh --ingest      # Ingest into HyperSearch
./bootstrap.sh --serve       # Start API server at http://127.0.0.1:8000
```

### Manual Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Copy and customize config
cp hypersearch.example.yaml hypersearch.yaml

# Start the server
hypersearch serve
# or: python -m uvicorn hypersearch.server:app --host 0.0.0.0 --port 8000
```

---

## API Reference

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

### Example: Create → Ingest → Search

```bash
# 1. Create a collection
curl -X POST http://127.0.0.1:8000/v1/collections \
  -H "Content-Type: application/json" \
  -d '{"name": "vulnerabilities"}'

# 2. Ingest a CSV dataset
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/ingest \
  -F "file=@datasets/output/cve_summary.csv" \
  -F 'config={"search_template": "{cve} {description}", "metadata_columns": ["cve", "cvss_score", "severity"]}'

# 3. Search
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/search \
  -H "Content-Type: application/json" \
  -d '{"query": "remote code execution buffer overflow", "top_k": 5}'

# 4. Search with metadata filters
curl -X POST http://127.0.0.1:8000/v1/collections/vulnerabilities/search \
  -H "Content-Type: application/json" \
  -d '{"query": "SQL injection", "top_k": 10, "filters": {"severity": "CRITICAL"}}'
```

### Search Filter Operators

```json
{
  "filters": {
    "severity": "HIGH",
    "cvss_score": {"$gte": 7.0},
    "cwe_id": {"$in": ["CWE-79", "CWE-89"]},
    "cvss_score": {"$between": [5.0, 9.0]}
  }
}
```

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

## CLI

HyperSearch includes a full CLI for all operations:

```bash
# Start the API server
hypersearch serve --host 0.0.0.0 --port 8000

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

# All commands support --config to specify a custom YAML file
hypersearch serve --config /path/to/hypersearch.yaml
```

---

## Demo Datasets

HyperSearch ships with scripts to fetch real-world security datasets:

### CVE Summary (Recommended for Quick Start)

Pre-enriched CVE records with descriptions and CVSS scores. ~30–50k rows, fast download.

```bash
python -m datasets.fetch_cve_summary --limit 50000
# Output: datasets/output/cve_summary.csv
```

### EPSS + NVD Enrichment

Full EPSS probability scores enriched with NVD vulnerability descriptions. Slower (NVD API rate-limited) but richer.

```bash
python -m datasets.fetch_epss --limit 10000
# Output: datasets/output/epss_enriched.csv

# With NVD API key for faster enrichment:
python -m datasets.fetch_epss --limit 50000 --nvd-api-key YOUR_KEY
```

---

## Benchmarking

The built-in benchmark harness measures ingestion throughput and search latency under concurrent load.

```bash
# Start the server first, then in another terminal:
python -m benchmarks.run \
  --collection bench_cve \
  --dataset datasets/output/cve_summary.csv \
  --concurrency 1,5,10

# Search-only (skip ingestion if collection already exists):
python -m benchmarks.run --collection bench_cve --search-only --concurrency 1,5,10,20
```

### Benchmark Output

Reports are saved to `benchmarks/reports/benchmark_<timestamp>.json`:

```json
{
  "ingestion": {
    "rows_ingested": 50000,
    "ingest_time_seconds": 142.5,
    "memory_delta_mb": 312.4
  },
  "search": {
    "concurrency_1":  { "p50_ms": 8.2,  "p95_ms": 14.1, "p99_ms": 22.3 },
    "concurrency_5":  { "p50_ms": 12.4, "p95_ms": 28.6, "p99_ms": 41.2 },
    "concurrency_10": { "p50_ms": 18.9, "p95_ms": 45.3, "p99_ms": 67.8 },
    "steady_state_memory_mb": 487.2
  }
}
```

---

## Configuration

Copy `hypersearch.example.yaml` to `hypersearch.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  workers: 1                        # Keep at 1 unless you have >8GB RAM
  api_key: null                     # Set a string to enable X-API-Key auth

storage:
  data_dir: "./data"                # Base directory for .duckdb collection files
  snapshot_dir: "./snapshots"       # Directory for backup snapshots

embedding:
  model: "all-MiniLM-L6-v2"        # Any sentence-transformers model name
  batch_size: 256                   # Rows per embedding batch (lower = less RAM)
  device: "cpu"                     # "cpu" or "cuda"
  dimensions: 384                   # Must match the model's output dimensionality

defaults:
  search_template: "{text}"         # Column interpolation for document construction
  metadata_columns: []              # Columns to preserve as filterable metadata
  top_k: 10                         # Default number of search results
```

### Environment Variable Overrides

Any config value can be overridden via environment variables with the `HYPER_` prefix:

```bash
HYPER_SERVER__PORT=9000 HYPER_SERVER__API_KEY=secret123 hypersearch serve
```

---

## Project Structure

```
StaticSearch/
├── bootstrap.sh                # One-command setup script
├── pyproject.toml              # Build system + dependency manifest
├── hypersearch.example.yaml    # Reference configuration
├── openapi.json                # Generated OpenAPI specification
│
├── hypersearch/                # Core package
│   ├── __init__.py             # Package version
│   ├── config.py               # YAML + env config loader
│   ├── models.py               # Pydantic request/response schemas
│   ├── db.py                   # DuckDB connection lifecycle
│   ├── engine.py               # Vector search execution
│   ├── ingest.py               # BYOD data processing pipeline
│   ├── embeddings.py           # Sentence-transformer embedding manager
│   ├── hot_reload.py           # Atomic staging + pointer swap
│   ├── snapshot.py             # Backup/restore file operations
│   ├── metrics.py              # Latency histograms + system stats
│   ├── middleware.py           # API-key authentication
│   ├── server.py               # FastAPI app factory
│   ├── cli.py                  # Typer CLI
│   └── routes/                 # Endpoint routers
│       ├── collections.py      # CRUD for collections
│       ├── ingest.py           # Dataset upload
│       ├── search.py           # Vector search
│       ├── snapshots.py        # Backup/restore
│       └── health.py           # Health + Prometheus metrics
│
├── datasets/                   # Dataset acquisition scripts
│   ├── fetch_epss.py           # EPSS + NVD enrichment
│   └── fetch_cve_summary.py   # Pre-enriched CVE summaries
│
├── benchmarks/                 # Performance harness
│   └── run.py                  # Benchmark runner
│
├── scripts/                    # Utility scripts
│   └── export_openapi.py       # OpenAPI spec exporter
│
├── tests/                      # Test suite
│   ├── conftest.py
│   ├── test_collections.py
│   ├── test_ingest.py
│   ├── test_search.py
│   └── test_snapshots.py
│
└── plan/                       # Architecture documentation
    └── StaticSearch.md
```

---

## OpenAPI Specification

Export the auto-generated OpenAPI spec:

```bash
# JSON format
python scripts/export_openapi.py

# YAML format
python scripts/export_openapi.py --format yaml --output openapi.yaml

# Or via bootstrap
./bootstrap.sh --openapi
```

The spec is also available live at `http://127.0.0.1:8000/openapi.json` when the server is running.

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
│  _hyper_vector_      │     Batched, lazy-loaded
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Staging DuckDB      │  ← {name}.staging.duckdb
│  HNSW Index Build    │
└─────────┬───────────┘
          │
          ▼ os.replace() (atomic)
┌─────────────────────┐
│  Live DuckDB         │  ← {name}.duckdb
│  Serving Queries     │     Zero-downtime swap
└─────────────────────┘
```

---

## License

MIT
