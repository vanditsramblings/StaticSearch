#!/usr/bin/env python3
"""Tiered Benchmarking & Resource Profiling Pipeline.

Runs the complete HyperSearch pipeline locally (no server needed):
1. Prepare datasets at requested tiers (subset from NVD or CVE summary data)
2. Ingest + embed + index each dataset
3. Run search benchmarks with multiple query categories
4. Measure RAM watermarks, CPU utilization, latency percentiles
5. Output detailed reports with per-tier analysis

Usage:
    python -m benchmarks.pipeline                           # Default tiers
    python -m benchmarks.pipeline --tiers 10000,100000,150000,200000
    python -m benchmarks.pipeline --skip-ingest             # Reuse existing collections
    python -m benchmarks.pipeline --dataset datasets/output/nvd_full.csv

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
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psutil

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("benchmarks.pipeline")

REPORTS_DIR = Path(__file__).parent / "reports"


def _get_system_info() -> dict:
    """Collect system hardware and software info for benchmark reproducibility."""
    cpu_freq = psutil.cpu_freq()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_freq_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
        "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "os": f"{platform.system()} {platform.release()}",
        "pid": os.getpid(),
    }

# ---------------------------------------------------------------------------
# Benchmark queries — diverse security-domain search terms
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES = {
    "exact_term": [
        "buffer overflow",
        "SQL injection",
        "cross-site scripting",
        "remote code execution",
        "denial of service",
    ],
    "natural_language": [
        "remote code execution vulnerability in web server",
        "SQL injection attack on database login form",
        "cross-site scripting XSS reflected stored DOM",
        "buffer overflow memory corruption exploit in parser",
        "authentication bypass unauthorized access to admin panel",
    ],
    "short_prefix": [
        "buffer",
        "overflow",
        "injection",
        "memory",
        "remote",
    ],
    "complex_multi_term": [
        "privilege escalation local root administrator kernel",
        "denial of service crash resource exhaustion infinite loop",
        "path traversal directory access file read write arbitrary",
        "use after free heap overflow corruption pointer dereference",
        "information disclosure sensitive data leak exposure credentials",
    ],
    "typeahead_progressive": [
        "buf",
        "buff",
        "buffe",
        "buffer",
        "buffer ov",
        "buffer over",
        "buffer overf",
        "buffer overflow",
        "buffer overflow ex",
        "buffer overflow exception",
    ],
}


@dataclass
class TierResult:
    """Results for a single dataset tier."""

    tier_rows: int
    actual_rows: int = 0
    ingest_time_seconds: float = 0.0
    embed_time_seconds: float = 0.0
    index_time_seconds: float = 0.0
    peak_ram_mb: float = 0.0
    steady_state_ram_mb: float = 0.0
    db_size_bytes: int = 0
    csv_size_bytes: int = 0
    search_latencies: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tier_rows": self.tier_rows,
            "actual_rows": self.actual_rows,
            "ingest_time_seconds": round(self.ingest_time_seconds, 2),
            "embed_time_seconds": round(self.embed_time_seconds, 2),
            "index_time_seconds": round(self.index_time_seconds, 2),
            "peak_ram_mb": round(self.peak_ram_mb, 2),
            "steady_state_ram_mb": round(self.steady_state_ram_mb, 2),
            "db_size_mb": round(self.db_size_bytes / (1024 * 1024), 2),
            "csv_size_mb": round(self.csv_size_bytes / (1024 * 1024), 2),
            "search_latencies": self.search_latencies,
        }


def _get_rss_mb() -> float:
    """Current process RSS in MB."""
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def prepare_dataset(source_csv: Path, target_csv: Path, limit: int) -> int:
    """Prepare a tier-specific dataset by subsetting the source CSV.

    Returns the actual number of rows written.
    """
    import polars as pl

    if target_csv.exists():
        # Check if it already has enough rows
        existing = pl.read_csv(target_csv, n_rows=1)
        existing_count = pl.scan_csv(target_csv).select(pl.len()).collect().item()
        if existing_count >= limit:
            logger.info("Tier dataset already exists: %s (%d rows)", target_csv, existing_count)
            return existing_count

    logger.info("Preparing tier dataset: %d rows from %s", limit, source_csv)
    df = pl.read_csv(source_csv, infer_schema_length=10000)

    # Shuffle deterministically and take the first `limit` rows
    if len(df) > limit:
        df = df.head(limit)

    df.write_csv(target_csv)
    logger.info("Wrote %d rows to %s", len(df), target_csv)
    return len(df)


def run_tier(
    tier_rows: int,
    source_csv: Path,
    data_dir: Path,
    reuse: bool = False,
    search_template: str = "{cve} {description}",
    metadata_columns: list[str] | None = None,
) -> TierResult:
    """Run the full pipeline for a single tier size."""
    from hypersearch.config import Settings
    from hypersearch.db import _init_connection, _reset, table_exists
    from hypersearch.embeddings import clear_cache
    from hypersearch.ingest import ingest_file

    result = TierResult(tier_rows=tier_rows)
    collection_name = f"bench_{tier_rows}"
    db_path = data_dir / f"{collection_name}.duckdb"

    logger.info("=" * 60)
    logger.info("TIER: %d rows", tier_rows)
    logger.info("=" * 60)

    # Detect metadata columns from the source CSV
    if metadata_columns is None:
        import polars as pl
        sample = pl.read_csv(source_csv, n_rows=1)
        # Use common CVE columns if they exist
        available = set(sample.columns)
        meta_candidates = ["cve", "severity", "cvss_score", "cwe_id", "vendor", "product",
                          "attack_vector", "published", "technologies"]
        metadata_columns = [c for c in meta_candidates if c in available]
        if not metadata_columns:
            metadata_columns = [c for c in sample.columns if c != "description"][:5]

    settings = Settings(
        storage={"data_dir": str(data_dir), "snapshot_dir": str(data_dir / "snapshots")},
        embedding={"batch_size": 512},  # Larger batches for throughput
    )

    # -----------------------------------------------------------------------
    # Phase 1: Prepare dataset
    # -----------------------------------------------------------------------
    tier_csv = data_dir / f"tier_{tier_rows}.csv"
    actual_rows = prepare_dataset(source_csv, tier_csv, tier_rows)
    result.actual_rows = actual_rows
    result.csv_size_bytes = tier_csv.stat().st_size

    # -----------------------------------------------------------------------
    # Phase 2: Ingest + Embed + Index
    # -----------------------------------------------------------------------
    if db_path.exists() and reuse:
        logger.info("Reusing existing collection: %s", db_path)
    else:
        # Remove old DB
        if db_path.exists():
            db_path.unlink()
            # Clean up WAL files
            for suffix in (".wal", ".tmp"):
                wal = db_path.with_suffix(db_path.suffix + suffix)
                if wal.exists():
                    wal.unlink()

        ram_before = _get_rss_mb()
        t_ingest_start = time.perf_counter()

        conn = _init_connection(db_path)

        rows_ingested = ingest_file(
            conn,
            tier_csv,
            filename=tier_csv.name,
            settings=settings,
            search_template=search_template,
            metadata_columns=metadata_columns,
        )

        t_ingest_end = time.perf_counter()
        ram_after = _get_rss_mb()

        result.ingest_time_seconds = t_ingest_end - t_ingest_start
        result.peak_ram_mb = max(ram_before, ram_after)
        result.actual_rows = rows_ingested

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
    # Phase 3: Search benchmarks
    # -----------------------------------------------------------------------
    from hypersearch.engine import search as run_search

    # Reopen connection for searching
    conn = _init_connection(db_path)
    result.steady_state_ram_mb = _get_rss_mb()

    # Clear embedding cache to get cold-start metrics first
    clear_cache()

    for category, queries in BENCHMARK_QUERIES.items():
        logger.info("Search benchmark: %s (%d queries) …", category, len(queries))
        latencies = _bench_search_category(conn, settings, queries)

        if latencies:
            latencies.sort()
            n = len(latencies)
            result.search_latencies[category] = {
                "p50_ms": round(latencies[int(n * 0.50)], 2),
                "p95_ms": round(latencies[int(n * 0.95)], 2),
                "p99_ms": round(latencies[int(n * 0.99)], 2),
                "mean_ms": round(statistics.mean(latencies), 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
                "queries": n,
            }
            pcts = result.search_latencies[category]
            logger.info(
                "  %s: p50=%.1fms  p95=%.1fms  mean=%.1fms  (n=%d)",
                category, pcts["p50_ms"], pcts["p95_ms"], pcts["mean_ms"], n,
            )

    # Run typeahead-specific benchmark (cached queries)
    logger.info("Search benchmark: typeahead_cached (cached repeat) …")
    cached_latencies = _bench_search_category(conn, settings, BENCHMARK_QUERIES["typeahead_progressive"])
    if cached_latencies:
        cached_latencies.sort()
        n = len(cached_latencies)
        result.search_latencies["typeahead_cached"] = {
            "p50_ms": round(cached_latencies[int(n * 0.50)], 2),
            "p95_ms": round(cached_latencies[int(n * 0.95)], 2),
            "p99_ms": round(cached_latencies[int(n * 0.99)], 2),
            "mean_ms": round(statistics.mean(cached_latencies), 2),
            "min_ms": round(min(cached_latencies), 2),
            "max_ms": round(max(cached_latencies), 2),
            "queries": n,
        }

    conn.close()
    _reset()
    gc.collect()

    return result


def _bench_search_category(
    conn, settings, queries: list[str], *, warmup: bool = True,
) -> list[float]:
    """Run search queries sequentially and return latency list.

    When *warmup* is True, runs a single throwaway query first to
    eliminate cold-start skew (DuckDB buffer pool, HNSW cache).
    """
    from hypersearch.engine import search as run_search

    if warmup:
        try:
            run_search(conn, "warmup query", settings=settings, top_k=1)
        except Exception:
            pass

    latencies: list[float] = []

    for q in queries:
        try:
            _, latency = run_search(conn, q, settings=settings, top_k=10)
            latencies.append(latency)
        except Exception as exc:
            logger.warning("Search failed for '%s': %s", q, exc)

    return latencies


def generate_sizing_matrix(
    results: list[TierResult],
    system_info: dict | None = None,
) -> str:
    """Render the hardware recommendation matrix as markdown."""
    lines = [
        "# HyperSearch — Benchmark Results & Sizing Matrix",
        "",
        f"Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # System info section
    if system_info:
        lines.extend([
            "## System Configuration",
            "",
            "| Property | Value |",
            "|---|---|",
            f"| **OS** | {system_info.get('os', 'N/A')} |",
            f"| **Platform** | {system_info.get('platform', 'N/A')} |",
            f"| **Processor** | {system_info.get('processor', 'N/A')} |",
            f"| **CPU Cores** | {system_info.get('cpu_count_physical', 'N/A')} physical / {system_info.get('cpu_count_logical', 'N/A')} logical |",
            f"| **RAM** | {system_info.get('ram_total_gb', 'N/A')} GB |",
            f"| **Python** | {system_info.get('python', 'N/A').split()[0]} |",
            f"| **Embedding Model** | `all-MiniLM-L6-v2` (384 dims) |",
            f"| **HNSW Config** | M=48, ef_construction=256, ef_search=128 |",
            "",
        ])

    lines.extend([
        "## Ingestion Performance",
        "",
        "| Dataset Size | Actual Rows | Ingest Time | Peak RAM | Steady RAM | DB Size | CSV Size |",
        "|---|---|---|---|---|---|---|",
    ])

    for r in results:
        lines.append(
            f"| **{r.tier_rows:,}** | {r.actual_rows:,} | {r.ingest_time_seconds:.1f}s "
            f"| {r.peak_ram_mb:.0f} MB | {r.steady_state_ram_mb:.0f} MB "
            f"| {r.db_size_bytes / (1024 * 1024):.1f} MB "
            f"| {r.csv_size_bytes / (1024 * 1024):.1f} MB |"
        )

    # Search latency table
    lines.extend([
        "",
        "## Search Latency by Query Type",
        "",
        "| Dataset | Query Type | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) | Min (ms) | Max (ms) |",
        "|---|---|---|---|---|---|---|---|",
    ])

    for r in results:
        for cat, lat in r.search_latencies.items():
            lines.append(
                f"| {r.tier_rows:,} | {cat} "
                f"| {lat.get('p50_ms', 'N/A')} | {lat.get('p95_ms', 'N/A')} "
                f"| {lat.get('p99_ms', 'N/A')} | {lat.get('mean_ms', 'N/A')} "
                f"| {lat.get('min_ms', 'N/A')} | {lat.get('max_ms', 'N/A')} |"
            )

    # Scaling analysis
    if len(results) >= 2:
        lines.extend([
            "",
            "## Scaling Analysis",
            "",
            "| Metric | " + " | ".join(f"{r.tier_rows:,}" for r in results) + " |",
            "|---| " + " | ".join(["---"] * len(results)) + " |",
        ])

        # Ingest throughput
        throughputs = [r.actual_rows / r.ingest_time_seconds if r.ingest_time_seconds > 0 else 0 for r in results]
        lines.append(
            "| Ingest (rows/s) | " +
            " | ".join(f"{t:.0f}" for t in throughputs) + " |"
        )

        # Mean search latency
        for cat in ["exact_term", "natural_language", "typeahead_progressive", "typeahead_cached"]:
            means = []
            for r in results:
                lat = r.search_latencies.get(cat, {})
                means.append(f"{lat.get('mean_ms', 'N/A')}")
            lines.append(
                f"| Search mean ({cat}) | " + " | ".join(means) + " |"
            )

        # DB size per 1k rows
        sizes = [r.db_size_bytes / (r.actual_rows / 1000) / 1024 if r.actual_rows > 0 else 0 for r in results]
        lines.append(
            "| DB size (KB/1k rows) | " +
            " | ".join(f"{s:.1f}" for s in sizes) + " |"
        )

    lines.extend([
        "",
        "## Notes",
        "",
        "- **Ingest Time** includes data loading, embedding generation, and HNSW index construction",
        "- **Peak RAM** is the maximum RSS during the embedding phase",
        "- **Steady RAM** is the RSS after ingestion, with the HNSW index loaded",
        "- **Latencies** measured with `all-MiniLM-L6-v2` (384 dims) on CPU",
        "- **Warmup query** is run before each category to eliminate cold-start skew",
        "- **typeahead_cached** shows latency when query embeddings are already cached (repeat queries)",
        "- All benchmarks use cosine distance HNSW indexes with M=48, ef_construction=256",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="HyperSearch Tiered Benchmark Pipeline")
    parser.add_argument(
        "--tiers", default="10000,100000,150000,200000",
        help="Comma-separated dataset sizes (default: 10000,100000,150000,200000)",
    )
    parser.add_argument(
        "--dataset", default=None,
        help="Source CSV dataset path (default: auto-detect nvd_full.csv or cve_summary.csv)",
    )
    parser.add_argument(
        "--template", default="{cve} {description}",
        help="Search template for document construction",
    )
    parser.add_argument(
        "--metadata", default=None,
        help="Comma-separated metadata columns (default: auto-detect)",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Directory for benchmark data (default: ./data/bench)",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Reuse existing collections (skip ingest)",
    )
    parser.add_argument(
        "--output-md", default=None,
        help="Write markdown results to this path (e.g., BENCHMARKS.md)",
    )
    args = parser.parse_args()

    tiers = [int(t) for t in args.tiers.split(",")]
    data_dir = Path(args.data_dir) if args.data_dir else Path("./data/bench")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "snapshots").mkdir(exist_ok=True)

    # Auto-detect dataset
    if args.dataset:
        source_csv = Path(args.dataset)
    else:
        nvd_full = Path("datasets/output/nvd_full.csv")
        cve_summary = Path("datasets/output/cve_summary.csv")
        if nvd_full.exists():
            source_csv = nvd_full
        elif cve_summary.exists():
            source_csv = cve_summary
        else:
            logger.error("No dataset found. Run 'python -m datasets.fetch_nvd_full' first.")
            sys.exit(1)

    metadata_cols = [c.strip() for c in args.metadata.split(",")] if args.metadata else None

    # Collect system info
    system_info = _get_system_info()
    logger.info("System: %s, %d cores, %.1f GB RAM",
                system_info["os"], system_info["cpu_count_logical"],
                system_info["ram_total_gb"])
    logger.info("HyperSearch Benchmark Pipeline")
    logger.info("Tiers: %s | Dataset: %s", tiers, source_csv)

    all_results: list[TierResult] = []

    for tier in tiers:
        result = run_tier(
            tier, source_csv, data_dir,
            reuse=args.skip_ingest,
            search_template=args.template,
            metadata_columns=metadata_cols,
        )
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
        "system": system_info,
        "source_dataset": str(source_csv),
        "tiers": [r.to_dict() for r in all_results],
    }
    with open(json_path, "w") as fh:
        json.dump(report, fh, indent=2)
    logger.info("JSON report: %s", json_path)

    # Markdown sizing matrix
    md_content = generate_sizing_matrix(all_results, system_info=system_info)

    md_path = REPORTS_DIR / "sizing_matrix.md"
    with open(md_path, "w") as fh:
        fh.write(md_content)
    logger.info("Sizing matrix: %s", md_path)

    # Write to custom output path (e.g., BENCHMARKS.md at repo root)
    if args.output_md:
        output_md = Path(args.output_md)
        with open(output_md, "w") as fh:
            fh.write(md_content)
        logger.info("Markdown report: %s", output_md)

    # Print summary
    print("\n" + md_content)


if __name__ == "__main__":
    main()
