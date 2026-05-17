"""Atomic hot-reload — stage ingestion to a secondary file, then pointer-swap."""

from __future__ import annotations

import io
import logging
import shutil
import uuid
from pathlib import Path

from hypersearch.config import Settings

logger = logging.getLogger(__name__)


def staging_path(settings: Settings, name: str) -> Path:
    """Return the staging database path for a collection."""
    return settings.storage.data_dir / f"{name}.staging.duckdb"


def prepare_staging(settings: Settings, name: str) -> Path:
    """Create a staging copy of the live database (or an empty staging file).

    Returns the path to the staging database file.
    """
    live = settings.storage.data_dir / f"{name}.duckdb"
    stage = staging_path(settings, name)

    if live.exists():
        shutil.copy2(live, stage)
        logger.info("Copied live DB → staging: %s", stage)
    else:
        # Touch so DuckDB can open it as a new file
        stage.touch()
        logger.info("Created empty staging file: %s", stage)

    return stage


def promote_staging(settings: Settings, name: str) -> None:
    """Atomically replace the live database with the staging copy.

    Uses ``Path.replace()`` which maps to ``os.replace()`` — atomic on POSIX
    when source and target are on the same filesystem.
    """
    from hypersearch.db import swap_connection

    stage = staging_path(settings, name)
    if not stage.exists():
        raise FileNotFoundError(f"No staging file for collection '{name}'")

    swap_connection(settings, name, stage)
    logger.info("Promoted staging → live for '%s'", name)


def cleanup_staging(settings: Settings, name: str) -> None:
    """Remove the staging file if it exists (e.g., after a failed ingest)."""
    stage = staging_path(settings, name)
    if stage.exists():
        stage.unlink()
        logger.info("Cleaned up staging file: %s", stage)


def run_staged_ingest(
    settings: Settings,
    name: str,
    source: Path | io.BytesIO,
    filename: str,
    *,
    search_template: str | None = None,
    metadata_columns: list[str] | None = None,
    batch_size: int | None = None,
) -> tuple[str, int]:
    """Full staged ingest workflow:

    1. Copy live → staging
    2. Ingest into staging DB
    3. Atomically promote staging → live

    Returns ``(task_id, rows_ingested)``.
    """
    import duckdb

    from hypersearch.db import _init_connection
    from hypersearch.ingest import ingest_file

    task_id = str(uuid.uuid4())
    stage = prepare_staging(settings, name)

    try:
        # Open a dedicated connection to the staging file
        conn = _init_connection(stage)

        rows = ingest_file(
            conn,
            source,
            filename=filename,
            settings=settings,
            search_template=search_template,
            metadata_columns=metadata_columns,
            batch_size=batch_size,
        )

        conn.close()
        promote_staging(settings, name)
        return task_id, rows

    except Exception:
        cleanup_staging(settings, name)
        raise
