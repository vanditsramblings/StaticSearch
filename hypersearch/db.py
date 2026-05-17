"""DuckDB connection lifecycle — open, migrate, install VSS, close."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

from hypersearch.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection registry — one open connection per collection
# ---------------------------------------------------------------------------

_connections: dict[str, duckdb.DuckDBPyConnection] = {}
_metadata: dict[str, dict[str, Any]] = {}


def _db_path(settings: Settings, name: str) -> Path:
    """Resolve the absolute path for a collection's database file."""
    return settings.storage.data_dir / f"{name}.duckdb"


def _init_connection(path: Path) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection and load the VSS extension."""
    conn = duckdb.connect(str(path))
    conn.execute("INSTALL vss; LOAD vss;")
    conn.execute("SET hnsw_enable_experimental_persistence = true;")
    logger.info("DuckDB connection opened: %s (VSS loaded)", path)
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def connect(settings: Settings, name: str) -> duckdb.DuckDBPyConnection:
    """Return an open connection for *name*, creating the DB file if needed."""
    if name in _connections:
        return _connections[name]

    path = _db_path(settings, name)
    conn = _init_connection(path)

    # Store creation timestamp if this is a brand-new collection
    if name not in _metadata:
        _metadata[name] = {"created_at": _read_creation_time(path)}

    _connections[name] = conn
    return conn


def disconnect(name: str) -> None:
    """Close and remove the connection for *name*."""
    conn = _connections.pop(name, None)
    if conn:
        conn.close()
        logger.info("DuckDB connection closed: %s", name)


def disconnect_all() -> None:
    """Shutdown hook — close every open connection."""
    for name in list(_connections):
        disconnect(name)


def drop(settings: Settings, name: str) -> None:
    """Close the connection and delete the database file."""
    disconnect(name)
    path = _db_path(settings, name)
    if path.exists():
        path.unlink()
        # Also remove WAL / tmp files DuckDB may leave behind
        for suffix in (".wal", ".tmp"):
            sidecar = path.with_suffix(path.suffix + suffix)
            if sidecar.exists():
                sidecar.unlink()
        logger.info("Deleted database file: %s", path)
    _metadata.pop(name, None)


def list_collections(settings: Settings) -> list[dict[str, Any]]:
    """Enumerate all ``.duckdb`` files in the data directory."""
    results: list[dict[str, Any]] = []
    data_dir = settings.storage.data_dir

    for db_file in sorted(data_dir.glob("*.duckdb")):
        name = db_file.stem
        # Skip staging files
        if name.endswith(".staging"):
            continue

        conn = connect(settings, name)
        row_count = _count_rows(conn)
        results.append({
            "name": name,
            "created_at": _metadata.get(name, {}).get("created_at"),
            "row_count": row_count,
            "size_bytes": db_file.stat().st_size,
        })

    return results


def table_exists(conn: duckdb.DuckDBPyConnection, table: str = "documents") -> bool:
    """Check whether *table* exists in the connection."""
    result = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(result and result[0] > 0)


def swap_connection(settings: Settings, name: str, new_path: Path) -> None:
    """Replace the active connection with one pointing at *new_path*.

    Used by the hot-reload system after staging completes.
    """
    disconnect(name)
    target = _db_path(settings, name)
    # Atomic replace — POSIX guarantees this on the same filesystem
    new_path.replace(target)
    _connections[name] = _init_connection(target)
    logger.info("Hot-swapped connection for '%s'", name)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _count_rows(conn: duckdb.DuckDBPyConnection) -> int:
    if not table_exists(conn):
        return 0
    result = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    return result[0] if result else 0


def _read_creation_time(path: Path) -> str:
    """Return the file creation time as an ISO string."""
    from datetime import datetime, timezone

    stat = path.stat()
    ts = getattr(stat, "st_birthtime", stat.st_mtime)
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
