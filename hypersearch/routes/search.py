"""Search endpoint — vector similarity with metadata filters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from hypersearch.config import Settings, get_settings
from hypersearch.db import connect, table_exists
from hypersearch.engine import search as run_search
from hypersearch.metrics import get_metrics, MetricsCollector
from hypersearch.models import SearchRequest, SearchResponse, SearchResult

router = APIRouter(prefix="/v1/collections", tags=["search"])


@router.post(
    "/{name}/search",
    response_model=SearchResponse,
    summary="Execute a semantic search query",
)
def search_collection(
    name: str,
    body: SearchRequest,
    settings: Settings = Depends(get_settings),
    collector: MetricsCollector = Depends(get_metrics),
) -> SearchResponse:
    """Run a vector similarity search with optional metadata filters.

    Returns ranked results sorted by cosine similarity (descending).
    """
    db_path = settings.storage.data_dir / f"{name}.duckdb"
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{name}' not found",
        )

    conn = connect(settings, name)
    if not table_exists(conn):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Collection '{name}' is empty — ingest data first",
        )

    try:
        results, latency_ms = run_search(
            conn,
            body.query,
            settings=settings,
            top_k=body.top_k,
            filters=body.filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    collector.record_query(latency_ms)

    return SearchResponse(
        results=[SearchResult(**r) for r in results],
        latency_ms=latency_ms,
    )
