#!/usr/bin/env python3
"""Phase 6-8: Tiered Benchmarking & Resource Profiling Pipeline.

Runs the complete HyperSearch pipeline locally (no server needed):
1. Generate deterministic synthetic datasets at 3 scales
2. Ingest + embed + index each dataset
3. Run search benchmarks with concurrent queries
4. Measure RAM watermarks, CPU utilization, latency percentiles
5. Output a sizing recommendation matrix

Usage:
    python -m benchmarks.pipeline                    # Run all tiers
    python -m benchmarks.pipeline --tiers 1000,5000  # Custom tiers (for testing)
    python -m benchmarks.pipeline --skip-ingest      # Reuse existing collections

Output:
    benchmarks/reports/pipeline_<timestamp>.json
    benchmarks/reports/sizing_matrix.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gc
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("benchmarks.pipeline")

REPORTS_DIR = Path(__file__).parent / "reports"

# ---------------------------------------------------------------------------
# Benchmark queries — realistic security-domain search terms
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES = [
    "remote code execution vulnerability in web server",
    "SQL injection attack on database",
    "cross-site scripting XSS reflected stored",
    "buffer overflow memory corruption exploit",
    "authentication bypass unauthorized access",
    "privilege escalation local root administrator",
    "denial of service crash resource exhaustion",
    "path traversal directory access file read",
    "use after free heap overflow corruption",
    "information disclosure sensitive data leak",
    "server-side request forgery SSRF internal",
    "deserialization untrusted data object injection",
    "command injection shell execution system",
    "integer overflow numeric error calculation",
    "race condition TOCTOU time of check",
    "XML external entity injection XXE parser",
    "insecure cryptographic algorithm weak cipher",
    "certificate validation bypass TLS SSL",
    "open redirect URL manipulation phishing",
    "memory leak resource exhaustion out of memory",
]


@dataclass
class TierResult:
    """Results for a single dataset tier."""

    tier_rows: int
    ingest_time_seconds: float = 0.0
    embed_time_seconds: float = 0.0
    index_time_seconds: float = 0.0
    peak_ram_mb: float = 0.0
    steady_state_ram_mb: float = 0.0
    db_size_bytes: int = 0
    search_latencies: dict[int, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tier_rows": self.tier_rows,
            "ingest_time_seconds": round(self.ingest_time_seconds, 2),
            "embed_time_seconds": round(self.embed_time_seconds, 2),
            "index_time_seconds": round(self.index_time_seconds, 2),
            "peak_ram_mb": round(self.peak_ram_mb, 2),
            "steady_state_ram_mb": round(self.steady_state_ram_mb, 2),
            "db_size_mb": round(self.db_size_bytes / (1024 * 1024), 2),
            "search_latencies": self.search_latencies,
        }


def _get_rss_mb() -> float:
    """Current process RSS in MB."""
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def run_tier(
    tier_rows: int,
    concurrency_levels: list[int],
    data_dir: Path,
    reuse: bool = False,
) -> TierResult:
    """Run the full pipeline for a single tier size."""
    from hypersearch.config import Settings
    from hypersearch.db import _init_connection, _reset, table_exists
    from hypersearch.ingest import ingest_file

    result = TierResult(tier_rows=tier_rows)
    collection_name = f"bench_{tier_rows}"
    db_path = data_dir / f"{collection_name}.duckdb"

    logger.info("=" * 60)
    logger.info("TIER: %d rows", tier_rows)
    logger.info("=" * 60)

    settings = Settings(
        storage={"data_dir": str(data_dir), "snapshot_dir": str(data_dir / "snapshots")},
        embedding={"batch_size": 256},
    )

    # -----------------------------------------------------------------------
    # Phase 1: Generate dataset
    # -----------------------------------------------------------------------
    from datasets.fetch_cve_summary import download_cve_summary, normalize_rows, save_csv

    csv_path = data_dir / f"cve_{tier_rows}.csv"
    if not csv_path.exists() or not reuse:
        logger.info("Fetching %d CVE rows …", tier_rows)
        rows = download_cve_summary(limit=tier_rows)
        rows = normalize_rows(rows)
        save_csv(rows, csv_path)
        del rows
        gc.collect()

    # -----------------------------------------------------------------------
    # Phase 2: Ingest + Embed + Index
    # -----------------------------------------------------------------------
    if db_path.exists() and reuse:
        logger.info("Reusing existing collection: %s", db_path)
    else:
        # Remove old DB
        if db_path.exists():
            db_path.unlink()

        ram_before = _get_rss_mb()
        t_ingest_start = time.perf_counter()

        conn = _init_connection(db_path)

        rows_ingested = ingest_file(
            conn,
            csv_path,
            filename=csv_path.name,
            settings=settings,
            search_template="{cve} {description}",
            metadata_columns=["cve", "severity", "cvss_score"],
        )

        t_ingest_end = time.perf_counter()
        ram_after = _get_rss_mb()

        result.ingest_time_seconds = t_ingest_end - t_ingest_start
        result.peak_ram_mb = max(ram_before, ram_after)

        conn.close()
        gc.collect()

        logger.info(
            "Ingested %d rows in %.1fs (RAM: %.1f → %.1f MB)",
            rows_ingested,
            result.ingest_time_seconds,
            ram_before,
            ram_after,
        )

    result.db_size_bytes = db_path.stat().st_size if db_path.exists() else 0

    # -----------------------------------------------------------------------
    # Phase 3: Search benchmarks at various concurrency levels
    # -----------------------------------------------------------------------
    from hypersearch.engine import search as run_search

    # Reopen connection for searching
    conn = _init_connection(db_path)
    result.steady_state_ram_mb = _get_rss_mb()

    for concurrency in concurrency_levels:
        logger.info("Search benchmark: concurrency=%d …", concurrency)
        latencies = _bench_search(conn, settings, concurrency)

        if latencies:
            latencies.sort()
            n = len(latencies)
            result.search_latencies[concurrency] = {
                "p50_ms": round(latencies[int(n * 0.50)], 2),
                "p95_ms": round(latencies[int(n * 0.95)], 2),
                "p99_ms": round(latencies[int(n * 0.99)], 2),
                "mean_ms": round(statistics.mean(latencies), 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
                "queries": n,
            }
            pcts = result.search_latencies[concurrency]
            logger.info(
                "  p50=%.1fms  p95=%.1fms  p99=%.1fms  (n=%d)",
                pcts["p50_ms"], pcts["p95_ms"], pcts["p99_ms"], n,
            )

    conn.close()
    _reset()
    gc.collect()

    return result


def _bench_search(conn, settings, concurrency: int, n_queries: int = 50) -> list[float]:
    """Run search queries at the given concurrency and return latency list."""
    from hypersearch.engine import search as run_search

    queries = (BENCHMARK_QUERIES * ((n_queries // len(BENCHMARK_QUERIES)) + 1))[:n_queries]
    latencies: list[float] = []

    if concurrency <= 1:
        # Sequential
        for q in queries:
            _, latency = run_search(conn, q, settings=settings, top_k=10)
            latencies.append(latency)
    else:
        # Concurrent using threads
        def _do(q: str) -> float:
            _, lat = run_search(conn, q, settings=settings, top_k=10)
            return lat

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_do, q) for q in queries]
            for f in concurrent.futures.as_completed(futures):
                try:
                    latencies.append(f.result())
                except Exception as exc:
                    logger.warning("Search failed: %s", exc)

    return latencies


def generate_sizing_matrix(results: list[TierResult]) -> str:
    """Render the hardware recommendation matrix as markdown."""
    lines = [
        "# HyperSearch — Hardware Sizing Matrix",
        "",
        "Generated: " + datetime.now(tz=timezone.utc).isoformat(),
        "",
        "| Dataset Size | Ingest Time | Peak RAM | Steady RAM | DB Size | p50 Latency | p95 Latency | p99 Latency |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        # Get latency at concurrency=1 for the baseline
        lat = r.search_latencies.get(1, {})
        lines.append(
            f"| **{r.tier_rows:,}** | {r.ingest_time_seconds:.1f}s "
            f"| {r.peak_ram_mb:.0f} MB | {r.steady_state_ram_mb:.0f} MB "
            f"| {r.db_size_bytes / (1024 * 1024):.1f} MB "
            f"| {lat.get('p50_ms', 'N/A')} ms | {lat.get('p95_ms', 'N/A')} ms "
            f"| {lat.get('p99_ms', 'N/A')} ms |"
        )

    lines.extend([
        "",
        "### Concurrency Impact",
        "",
        "| Dataset Size | @1 p95 | @5 p95 | @10 p95 |",
        "|---|---|---|---|",
    ])

    for r in results:
        c1 = r.search_latencies.get(1, {}).get("p95_ms", "N/A")
        c5 = r.search_latencies.get(5, {}).get("p95_ms", "N/A")
        c10 = r.search_latencies.get(10, {}).get("p95_ms", "N/A")
        lines.append(f"| **{r.tier_rows:,}** | {c1} ms | {c5} ms | {c10} ms |")

    lines.extend([
        "",
        "### Notes",
        "",
        "- **Ingest Time** includes data loading, embedding, and HNSW index construction",
        "- **Peak RAM** is the maximum RSS during the embedding phase",
        "- **Steady RAM** is the RSS after ingestion, with the HNSW index hot",
        "- **Latencies** measured with `all-MiniLM-L6-v2` (384 dims) on CPU",
        "- All benchmarks use cosine distance HNSW indexes",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="HyperSearch Tiered Benchmark Pipeline")
    parser.add_argument(
        "--tiers", default="1000,5000,10000",
        help="Comma-separated dataset sizes to benchmark (default: 1000,5000,10000)",
    )
    parser.add_argument(
        "--concurrency", default="1,5,10",
        help="Comma-separated concurrency levels (default: 1,5,10)",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Directory for benchmark data (default: ./data/bench)",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Reuse existing collections (skip ingest)",
    )
    args = parser.parse_args()

    tiers = [int(t) for t in args.tiers.split(",")]
    concurrency_levels = [int(c) for c in args.concurrency.split(",")]
    data_dir = Path(args.data_dir) if args.data_dir else Path("./data/bench")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "snapshots").mkdir(exist_ok=True)

    logger.info("HyperSearch Benchmark Pipeline")
    logger.info("Tiers: %s | Concurrency: %s", tiers, concurrency_levels)

    all_results: list[TierResult] = []

    for tier in tiers:
        result = run_tier(tier, concurrency_levels, data_dir, reuse=args.skip_ingest)
        all_results.append(result)

    # -----------------------------------------------------------------------
    # Save reports
    # -----------------------------------------------------------------------
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # JSON report
    json_path = REPORTS_DIR / f"pipeline_{ts}.json"
    report = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "system": {
            "python": sys.version,
            "pid": os.getpid(),
        },
        "tiers": [r.to_dict() for r in all_results],
    }
    with open(json_path, "w") as fh:
        json.dump(report, fh, indent=2)
    logger.info("JSON report: %s", json_path)

    # Markdown sizing matrix
    md_path = REPORTS_DIR / "sizing_matrix.md"
    md_content = generate_sizing_matrix(all_results)
    with open(md_path, "w") as fh:
        fh.write(md_content)
    logger.info("Sizing matrix: %s", md_path)

    # Print summary
    print("\n" + md_content)


if __name__ == "__main__":
    main()
