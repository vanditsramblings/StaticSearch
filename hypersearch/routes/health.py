"""Health and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from hypersearch.config import Settings, get_settings
from hypersearch.db import list_collections
from hypersearch.metrics import MetricsCollector, get_metrics
from hypersearch.models import HealthResponse

router = APIRouter(tags=["operational"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="System health check",
)
def health_check(
    settings: Settings = Depends(get_settings),
    collector: MetricsCollector = Depends(get_metrics),
) -> HealthResponse:
    """Return memory usage, disk space, active collection count, and uptime."""
    collections = list_collections(settings)
    return HealthResponse(
        status="ok",
        memory_mb=collector.memory_mb(),
        disk_free_gb=collector.disk_free_gb(),
        collections_active=len(collections),
        uptime_seconds=round(collector.uptime_seconds, 1),
    )


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
)
def prometheus_metrics(
    collector: MetricsCollector = Depends(get_metrics),
) -> str:
    """Return metrics in Prometheus text exposition format."""
    return collector.to_prometheus()
