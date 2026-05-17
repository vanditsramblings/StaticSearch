"""Pydantic request/response schemas — zero business logic lives here."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

class CreateCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9_\-]+$")
    description: str | None = None


class CollectionResponse(BaseModel):
    name: str
    created_at: datetime
    storage_path: str


class CollectionSummary(BaseModel):
    name: str
    created_at: datetime
    row_count: int
    size_bytes: int


class CollectionListResponse(BaseModel):
    collections: list[CollectionSummary]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestConfig(BaseModel):
    """Inline override for per-ingest settings (optional in multipart body)."""

    search_template: str | None = None
    metadata_columns: list[str] | None = None
    batch_size: int | None = None
    file_path: str | None = Field(
        None,
        description="Server-side file path. Use this instead of file upload for local CLI ingestion.",
    )


class IngestResponse(BaseModel):
    task_id: str
    status: str = "accepted"
    rows_queued: int


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    """Vector search query with optional metadata filters.

    Filter operators:
        - Simple equality: ``{"color": "red"}``
        - Rich operators:  ``{"price": {"$lt": 100}, "tags": {"$in": ["outdoor"]}}``

    Supported operators: ``$eq``, ``$neq``, ``$gt``, ``$gte``, ``$lt``, ``$lte``,
    ``$in``, ``$between``.
    """

    query: str = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=1000)
    filters: dict[str, Any] | None = None


class SearchResult(BaseModel):
    score: float
    document: str
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    results: list[SearchResult]
    latency_ms: float


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

class SnapshotResponse(BaseModel):
    snapshot_path: str
    size_bytes: int
    created_at: datetime


class RestoreRequest(BaseModel):
    snapshot_path: str


class RestoreResponse(BaseModel):
    restored: bool
    collection: str


# ---------------------------------------------------------------------------
# Health & Errors
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    memory_mb: float
    disk_free_gb: float
    collections_active: int
    uptime_seconds: float


class ErrorResponse(BaseModel):
    detail: str
    status_code: int
