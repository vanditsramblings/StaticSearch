#!/usr/bin/env python3
"""HyperSearch performance benchmarking harness.

Measures ingestion throughput, embedding time, peak RAM, and search latency
percentiles under simulated concurrent load.

Usage:
    python -m benchmarks.run --collection bench_test --dataset datasets/output/cve_summary.csv
    python -m benchmarks.run --collection bench_test --search-only  # skip ingestion
    python -m benchmarks.run --help

Output:
    benchmarks/reports/benchmark_<timestamp>.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent / "reports"


def _ensure_server_url(base_url: str) -> str:
    """Validate the server is reachable."""
    import urllib.request

    try:
        req = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            logger.info("Server healthy: %s", data)
            return base_url
    except Exception as exc:
        raise ConnectionError(
            f"Cannot reach HyperSearch at {base_url}/health — is the server running?"
        ) from exc


def benchmark_ingest(
    base_url: str,
    collection: str,
    dataset_path: str,
    search_template: str,
    metadata_columns: list[str],
) -> dict:
    """Benchmark the full ingest pipeline (upload → embed → index)."""
    import urllib.request

    logger.info("=== INGESTION BENCHMARK ===")

    # Track memory before
    mem_before = _get_memory(base_url)

    # Create collection
    _api_call(base_url, "POST", "/v1/collections", {"name": collection})

    # Ingest via file_path (server-side)
    config = json.dumps({
        "search_template": search_template,
        "metadata_columns": metadata_columns,
        "file_path": str(Path(dataset_path).resolve()),
    }).encode()

    t0 = time.perf_counter()

    req = urllib.request.Request(
        f"{base_url}/v1/collections/{collection}/ingest",
        data=config,
        headers={"Content-Type": "multipart/form-data"},
        method="POST",
    )

    # Use multipart form with config field
    boundary = "----HyperSearchBench"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="config"\r\n\r\n'
        f'{json.dumps({"search_template": search_template, "metadata_columns": metadata_columns, "file_path": str(Path(dataset_path).resolve())})}\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        f"{base_url}/v1/collections/{collection}/ingest",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=3600) as resp:
        result = json.loads(resp.read())

    ingest_time = time.perf_counter() - t0
    mem_after = _get_memory(base_url)

    logger.info("Ingest completed: %d rows in %.1fs", result["rows_queued"], ingest_time)
    logger.info("Peak RAM delta: %.1f MB", mem_after - mem_before)

    return {
        "rows_ingested": result["rows_queued"],
        "ingest_time_seconds": round(ingest_time, 2),
        "memory_before_mb": mem_before,
        "memory_after_mb": mem_after,
        "memory_delta_mb": round(mem_after - mem_before, 2),
    }


def benchmark_search(
    base_url: str,
    collection: str,
    queries: list[str],
    concurrency_levels: list[int],
    top_k: int = 10,
) -> dict:
    """Benchmark search latency under varying concurrent load.

    For each concurrency level, fires `queries` concurrently and records
    p50/p95/p99 latencies.
    """
    import urllib.request

    logger.info("=== SEARCH BENCHMARK ===")

    results = {}

    for concurrency in concurrency_levels:
        logger.info("Testing %d concurrent requests …", concurrency)
        latencies: list[float] = []

        def _do_search(query: str) -> float:
            payload = json.dumps({"query": query, "top_k": top_k}).encode()
            req = urllib.request.Request(
                f"{base_url}/v1/collections/{collection}/search",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            return (time.perf_counter() - t0) * 1000  # ms

        # Run queries in thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            # Repeat queries to fill at least 100 samples
            expanded_queries = (queries * ((100 // len(queries)) + 1))[:100]
            futures = [pool.submit(_do_search, q) for q in expanded_queries]

            for future in concurrent.futures.as_completed(futures):
                try:
                    latencies.append(future.result())
                except Exception as exc:
                    logger.warning("Search failed: %s", exc)

        if latencies:
            latencies.sort()
            n = len(latencies)
            pcts = {
                "p50_ms": round(latencies[int(n * 0.50)], 2),
                "p95_ms": round(latencies[int(n * 0.95)], 2),
                "p99_ms": round(latencies[int(n * 0.99)], 2),
                "mean_ms": round(statistics.mean(latencies), 2),
                "total_queries": n,
            }
            logger.info("  p50=%.1fms  p95=%.1fms  p99=%.1fms", pcts["p50_ms"], pcts["p95_ms"], pcts["p99_ms"])
        else:
            pcts = {"p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "mean_ms": 0, "total_queries": 0}

        results[f"concurrency_{concurrency}"] = pcts

    # Get steady-state memory
    mem = _get_memory(base_url)
    results["steady_state_memory_mb"] = mem
    logger.info("Steady-state RAM: %.1f MB", mem)

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_memory(base_url: str) -> float:
    """Fetch current RSS from /health."""
    import urllib.request

    req = urllib.request.Request(f"{base_url}/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return data["memory_mb"]


def _api_call(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    """Make a simple JSON API call."""
    import urllib.request

    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()) if resp.status != 204 else {}
    except urllib.request.HTTPError as exc:
        if exc.code == 409:
            logger.info("Collection already exists, continuing …")
            return {}
        raise


# Default search queries for CVE/security datasets
SECURITY_QUERIES = [
    "remote code execution vulnerability",
    "SQL injection attack",
    "cross-site scripting XSS",
    "buffer overflow memory corruption",
    "authentication bypass unauthorized access",
    "privilege escalation local root",
    "denial of service crash",
    "path traversal directory access",
    "use after free heap overflow",
    "information disclosure sensitive data",
    "server-side request forgery SSRF",
    "deserialization of untrusted data",
    "command injection shell execution",
    "integer overflow numeric error",
    "race condition TOCTOU",
    "open redirect URL manipulation",
    "XML external entity injection XXE",
    "insecure cryptographic algorithm",
    "certificate validation bypass TLS",
    "memory leak resource exhaustion",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="HyperSearch Benchmark Harness")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--collection", default="bench_cve", help="Collection name")
    parser.add_argument("--dataset", default="datasets/output/cve_summary.csv", help="Dataset CSV path")
    parser.add_argument("--template", default="{cve} {description}", help="Search template")
    parser.add_argument("--metadata", default="cve,cvss_score,severity", help="Comma-separated metadata columns")
    parser.add_argument("--concurrency", default="1,5,10", help="Comma-separated concurrency levels")
    parser.add_argument("--top-k", type=int, default=10, help="Search results count")
    parser.add_argument("--search-only", action="store_true", help="Skip ingestion benchmark")
    parser.add_argument("--output", type=str, default=None, help="Output JSON report path")
    args = parser.parse_args()

    base_url = _ensure_server_url(args.base_url)
    concurrency_levels = [int(c) for c in args.concurrency.split(",")]
    metadata_cols = [c.strip() for c in args.metadata.split(",")]

    report: dict = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "config": {
            "collection": args.collection,
            "dataset": args.dataset,
            "template": args.template,
            "metadata_columns": metadata_cols,
            "concurrency_levels": concurrency_levels,
            "top_k": args.top_k,
        },
    }

    # Ingestion benchmark
    if not args.search_only:
        report["ingestion"] = benchmark_ingest(
            base_url, args.collection, args.dataset, args.template, metadata_cols,
        )

    # Search benchmark
    report["search"] = benchmark_search(
        base_url, args.collection, SECURITY_QUERIES, concurrency_levels, args.top_k,
    )

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else REPORTS_DIR / f"benchmark_{ts}.json"

    with open(output_path, "w") as fh:
        json.dump(report, fh, indent=2)

    logger.info("Benchmark report saved to %s", output_path)

    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)

    if "ingestion" in report:
        ing = report["ingestion"]
        print(f"\nIngestion:")
        print(f"  Rows:      {ing['rows_ingested']:,}")
        print(f"  Time:      {ing['ingest_time_seconds']:.1f}s")
        print(f"  RAM delta: {ing['memory_delta_mb']:.1f} MB")

    if "search" in report:
        print(f"\nSearch Latency:")
        for key, val in report["search"].items():
            if key.startswith("concurrency_"):
                level = key.split("_")[1]
                print(f"  @{level} concurrent: p50={val['p50_ms']:.1f}ms  p95={val['p95_ms']:.1f}ms  p99={val['p99_ms']:.1f}ms")
        if "steady_state_memory_mb" in report["search"]:
            print(f"\n  Steady-state RAM: {report['search']['steady_state_memory_mb']:.1f} MB")

    print("=" * 60)


if __name__ == "__main__":
    main()
