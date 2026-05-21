"""Snapshot (backup) and restore — safe file-copy operations for DuckDB databases."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from hypersearch.config import Settings

logger = logging.getLogger(__name__)


def create_snapshot(settings: Settings, name: str) -> dict[str, str | int]:
    """Create a point-in-time snapshot of the collection database.

    The snapshot is a plain copy of the ``.duckdb`` file placed into
    ``settings.storage.snapshot_dir`` with a timestamped filename.

    Returns a dict with ``snapshot_path``, ``size_bytes``, and ``created_at``.
    """
    source = settings.storage.data_dir / f"{name}.duckdb"
    if not source.exists():
        raise FileNotFoundError(f"Collection '{name}' has no database file")

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = settings.storage.snapshot_dir / f"{name}_{ts}.duckdb"

    shutil.copy2(source, dest)
    size = dest.stat().st_size
    created = datetime.now(tz=timezone.utc).isoformat()

    logger.info("Snapshot created: %s (%d bytes)", dest, size)

    return {
        "snapshot_path": str(dest),
        "size_bytes": size,
        "created_at": created,
    }


def restore_snapshot(settings: Settings, name: str, snapshot_path: str) -> bool:
    """Restore a collection from a snapshot file.

    Closes the active connection, replaces the live database file with the
    snapshot, and re-opens the connection.

    Returns ``True`` on success.
    """
    from hypersearch.db import swap_connection

    snap = Path(snapshot_path)
    if not snap.exists():
        raise FileNotFoundError(f"Snapshot file not found: {snapshot_path}")

    # Copy the snapshot to a temp location, then atomic-swap
    target = settings.storage.data_dir / f"{name}.restoring.duckdb"
    shutil.copy2(snap, target)

    swap_connection(settings, name, target)
    logger.info("Restored collection '%s' from %s", name, snapshot_path)
    return True
