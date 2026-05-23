# HyperSearch — Benchmark Results & Sizing Matrix

Generated: 2026-05-21T19:32:42.242667+00:00

## Ingestion Performance

| Dataset Size | Actual Rows | Ingest Time | Peak RAM | Steady RAM | DB Size | CSV Size |
|---|---|---|---|---|---|---|
| **10,000** | 10,000 | 107.9s | 907 MB | 873 MB | 35.5 MB | 4.0 MB |
| **100,000** | 100,000 | 1365.2s | 1003 MB | 596 MB | 352.8 MB | 49.9 MB |
| **150,000** | 150,000 | 2140.1s | 918 MB | 233 MB | 623.8 MB | 78.4 MB |
| **200,000** | 200,000 | 3172.5s | 1071 MB | 483 MB | 803.0 MB | 107.0 MB |

## Search Latency by Query Type

| Dataset | Query Type | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) | Min (ms) | Max (ms) |
|---|---|---|---|---|---|---|---|
| 10,000 | exact_term | 20.15 | 113.5 | 113.5 | 37.76 | 17.05 | 113.5 |
| 10,000 | natural_language | 20.54 | 20.62 | 20.62 | 20.25 | 18.97 | 20.62 |
| 10,000 | short_prefix | 18.99 | 19.9 | 19.9 | 19.1 | 18.54 | 19.9 |
| 10,000 | complex_multi_term | 18.44 | 21.44 | 21.44 | 19.03 | 17.96 | 21.44 |
| 10,000 | typeahead_progressive | 18.32 | 20.35 | 20.35 | 16.88 | 9.1 | 20.35 |
| 10,000 | typeahead_cached | 10.76 | 11.75 | 11.75 | 10.52 | 8.8 | 11.75 |
| 100,000 | exact_term | 25.13 | 374.36 | 374.36 | 93.89 | 19.5 | 374.36 |
| 100,000 | natural_language | 29.05 | 30.26 | 30.26 | 29.28 | 28.43 | 30.26 |
| 100,000 | short_prefix | 28.55 | 35.68 | 35.68 | 29.37 | 24.04 | 35.68 |
| 100,000 | complex_multi_term | 29.41 | 30.29 | 30.29 | 28.94 | 27.49 | 30.29 |
| 100,000 | typeahead_progressive | 28.43 | 33.44 | 33.44 | 26.55 | 15.94 | 33.44 |
| 100,000 | typeahead_cached | 17.21 | 19.15 | 19.15 | 17.46 | 15.05 | 19.15 |
| 150,000 | exact_term | 36.43 | 638.73 | 638.73 | 155.51 | 26.58 | 638.73 |
| 150,000 | natural_language | 37.26 | 39.41 | 39.41 | 37.14 | 34.62 | 39.41 |
| 150,000 | short_prefix | 31.06 | 45.86 | 45.86 | 34.49 | 30.42 | 45.86 |
| 150,000 | complex_multi_term | 34.66 | 37.23 | 37.23 | 33.13 | 27.0 | 37.23 |
| 150,000 | typeahead_progressive | 29.9 | 39.45 | 39.45 | 29.14 | 17.48 | 39.45 |
| 150,000 | typeahead_cached | 19.21 | 20.47 | 20.47 | 18.27 | 16.2 | 20.47 |
| 200,000 | exact_term | 40.67 | 733.27 | 733.27 | 175.5 | 28.15 | 733.27 |
| 200,000 | natural_language | 43.16 | 45.64 | 45.64 | 40.27 | 30.84 | 45.64 |
| 200,000 | short_prefix | 43.92 | 48.65 | 48.65 | 42.59 | 36.34 | 48.65 |
| 200,000 | complex_multi_term | 35.28 | 40.86 | 40.86 | 35.64 | 30.67 | 40.86 |
| 200,000 | typeahead_progressive | 34.34 | 42.8 | 42.8 | 31.78 | 18.19 | 42.8 |
| 200,000 | typeahead_cached | 19.04 | 21.32 | 21.32 | 18.6 | 16.06 | 21.32 |

## Scaling Analysis

| Metric | 10,000 | 100,000 | 150,000 | 200,000 |
|---| --- | --- | --- | --- |
| Ingest (rows/s) | 93 | 73 | 70 | 63 |
| Search mean (exact_term) | 37.76 | 93.89 | 155.51 | 175.5 |
| Search mean (natural_language) | 20.25 | 29.28 | 37.14 | 40.27 |
| Search mean (typeahead_progressive) | 16.88 | 26.55 | 29.14 | 31.78 |
| Search mean (typeahead_cached) | 10.52 | 17.46 | 18.27 | 18.6 |
| DB size (KB/1k rows) | 3636.4 | 3612.3 | 4258.2 | 4111.4 |

## Notes

- **Ingest Time** includes data loading, embedding generation, and HNSW index construction
- **Peak RAM** is the maximum RSS during the embedding phase
- **Steady RAM** is the RSS after ingestion, with the HNSW index loaded
- **Latencies** measured with `all-MiniLM-L6-v2` (384 dims) on CPU
- **typeahead_cached** shows latency when query embeddings are already cached (repeat queries)
- All benchmarks use cosine distance HNSW indexes with M=48, ef_construction=256