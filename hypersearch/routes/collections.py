"""Collection CRUD endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from hypersearch.config import Settings, get_settings
from hypersearch.db import connect, drop, list_collections
from hypersearch.models import (
    CollectionListResponse,
    CollectionResponse,
    CollectionSummary,
    CreateCollectionRequest,
)

router = APIRouter(prefix="/v1/collections", tags=["collections"])


@router.post(
    "",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new collection",
)
def create_collection(
    body: CreateCollectionRequest,
    settings: Settings = Depends(get_settings),
) -> CollectionResponse:
    """Initialize a new collection — creates a standalone ``.duckdb`` file."""
    db_path = settings.storage.data_dir / f"{body.name}.duckdb"
    if db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Collection '{body.name}' already exists",
        )

    # Open (and create) the database + install VSS
    connect(settings, body.name)

    return CollectionResponse(
        name=body.name,
        created_at=datetime.utcnow(),
        storage_path=str(db_path),
    )


@router.get(
    "",
    response_model=CollectionListResponse,
    summary="List all active collections",
)
def list_all_collections(
    settings: Settings = Depends(get_settings),
) -> CollectionListResponse:
    """Enumerate all ``.duckdb`` files in the data directory."""
    raw = list_collections(settings)
    return CollectionListResponse(
        collections=[
            CollectionSummary(
                name=c["name"],
                created_at=c["created_at"] or datetime.utcnow().isoformat(),
                row_count=c["row_count"],
                size_bytes=c["size_bytes"],
            )
            for c in raw
        ]
    )


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection",
)
def delete_collection(
    name: str,
    settings: Settings = Depends(get_settings),
) -> None:
    """Drop a collection and delete its storage file."""
    db_path = settings.storage.data_dir / f"{name}.duckdb"
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{name}' not found",
        )
    drop(settings, name)
