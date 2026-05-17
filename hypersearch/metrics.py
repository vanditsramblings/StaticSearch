"""Instrumentation — latency histograms, RAM tracking, and Prometheus-format exposition."""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field

import psutil


@dataclass
class MetricsCollector:
    """Lightweight metrics sink — no external dependencies.

    Stores the last ``max_samples`` query latencies for percentile computation.
    """

    max_samples: int = 10_000
    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=10_000))
    _start_time: float = field(default_factory=time.monotonic)
    _query_count: int = 0

    def record_query(self, latency_ms: float) -> None:
        """Record a single query's latency in milliseconds."""
        self._latencies.append(latency_ms)
        self._query_count += 1

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def query_count(self) -> int:
        return self._query_count

    def percentiles(self) -> dict[str, float]:
        """Return p50, p95, p99 query latencies (ms). Empty → all zeros."""
        if not self._latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        data = sorted(self._latencies)
        n = len(data)
        return {
            "p50": round(data[int(n * 0.50)], 3),
            "p95": round(data[int(n * 0.95)], 3),
            "p99": round(data[int(n * 0.99)], 3),
        }

    @staticmethod
    def memory_mb() -> float:
        """Current RSS of this process in megabytes."""
        proc = psutil.Process()
        return round(proc.memory_info().rss / (1024 * 1024), 2)

    @staticmethod
    def disk_free_gb(path: str = "/") -> float:
        """Free disk space in gigabytes on the partition containing *path*."""
        usage = psutil.disk_usage(path)
        return round(usage.free / (1024**3), 2)

    def to_prometheus(self) -> str:
        """Render metrics in Prometheus text exposition format."""
        pcts = self.percentiles()
        lines = [
            "# HELP hypersearch_uptime_seconds Server uptime in seconds",
            "# TYPE hypersearch_uptime_seconds gauge",
            f"hypersearch_uptime_seconds {self.uptime_seconds:.1f}",
            "",
            "# HELP hypersearch_queries_total Total queries served",
            "# TYPE hypersearch_queries_total counter",
            f"hypersearch_queries_total {self._query_count}",
            "",
            "# HELP hypersearch_query_latency_ms Query latency percentiles",
            "# TYPE hypersearch_query_latency_ms gauge",
            f'hypersearch_query_latency_ms{{quantile="0.50"}} {pcts["p50"]}',
            f'hypersearch_query_latency_ms{{quantile="0.95"}} {pcts["p95"]}',
            f'hypersearch_query_latency_ms{{quantile="0.99"}} {pcts["p99"]}',
            "",
            "# HELP hypersearch_memory_rss_mb Process RSS in megabytes",
            "# TYPE hypersearch_memory_rss_mb gauge",
            f"hypersearch_memory_rss_mb {self.memory_mb()}",
            "",
            "# HELP hypersearch_disk_free_gb Free disk space in GB",
            "# TYPE hypersearch_disk_free_gb gauge",
            f"hypersearch_disk_free_gb {self.disk_free_gb()}",
        ]
        return "\n".join(lines) + "\n"


# Module-level singleton
_collector: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Return the global metrics collector (created on first call)."""
    global _collector  # noqa: PLW0603
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
