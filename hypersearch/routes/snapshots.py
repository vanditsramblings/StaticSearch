"""Snapshot and restore endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from hypersearch.config import Settings, get_settings
from hypersearch.models import RestoreRequest, RestoreResponse, SnapshotResponse
from hypersearch.snapshot import create_snapshot, restore_snapshot

router = APIRouter(prefix="/v1/collections", tags=["snapshots"])


@router.post(
    "/{name}/snapshot",
    response_model=SnapshotResponse,
    summary="Create a backup snapshot",
)
def take_snapshot(
    name: str,
    settings: Settings = Depends(get_settings),
) -> SnapshotResponse:
    """Hot-copy the collection database to a timestamped snapshot file."""
    try:
        result = create_snapshot(settings, name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return SnapshotResponse(**result)


@router.post(
    "/{name}/restore",
    response_model=RestoreResponse,
    summary="Restore from a snapshot",
)
def restore_from_snapshot(
    name: str,
    body: RestoreRequest,
    settings: Settings = Depends(get_settings),
) -> RestoreResponse:
    """Restore a collection from a previously created snapshot file."""
    try:
        restore_snapshot(settings, name, body.snapshot_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return RestoreResponse(restored=True, collection=name)
