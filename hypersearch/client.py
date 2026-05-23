"""HyperSearch — top-level client for programmatic library use.

This is the main entry point for using HyperSearch as a Python library::

    from hypersearch import HyperSearch

    with HyperSearch(data_dir="./my_data") as hs:
        col = hs.create_collection("products")
        col.ingest("products.csv", template="{name} {description}")
        results = col.search("waterproof hiking boots", top_k=5)
        for hit in results:
            print(hit.score, hit.document)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hypersearch.collection import Collection
from hypersearch.config import EmbeddingConfig, Settings, StorageConfig

logger = logging.getLogger(__name__)


class HyperSearch:
    """Main HyperSearch client — manages collections and configuration.

    Args:
        data_dir: Directory for ``.duckdb`` collection files.
            Created automatically if it doesn't exist.
        snapshot_dir: Directory for backup snapshots.
            Defaults to ``{data_dir}/../snapshots``.
        model: Sentence-transformer model name for embeddings.
        device: Compute device — ``"cpu"`` or ``"cuda"``.
        batch_size: Rows per embedding batch (lower = less RAM).
        hnsw_m: HNSW max edges per node (higher = better recall).
        hnsw_ef_construction: HNSW build-time beam width.
        hnsw_ef_search: HNSW query-time beam width.

    Example::

        # As context manager (recommended — auto-closes connections)
        with HyperSearch(data_dir="./data") as hs:
            col = hs.create_collection("products")
            col.ingest("products.csv", template="{name} {desc}")
            results = col.search("hiking boots")

        # Manual lifecycle
        hs = HyperSearch()
        try:
            col = hs.create_collection("test")
            col.ingest("data.csv")
        finally:
            hs.close()
    """

    def __init__(
        self,
        data_dir: str | Path = "./data",
        snapshot_dir: str | Path | None = None,
        *,
        model: str = "all-MiniLM-L6-v2",
        device: str = "cpu",
        batch_size: int = 256,
        hnsw_m: int = 48,
        hnsw_ef_construction: int = 256,
        hnsw_ef_search: int = 128,
    ) -> None:
        data_path = Path(data_dir)
        snap_path = Path(snapshot_dir) if snapshot_dir else data_path.parent / "snapshots"

        data_path.mkdir(parents=True, exist_ok=True)
        snap_path.mkdir(parents=True, exist_ok=True)

        self._settings = Settings(
            storage=StorageConfig(data_dir=data_path, snapshot_dir=snap_path),
            embedding=EmbeddingConfig(
                model=model,
                device=device,
                batch_size=batch_size,
                hnsw_m=hnsw_m,
                hnsw_ef_construction=hnsw_ef_construction,
                hnsw_ef_search=hnsw_ef_search,
            ),
        )
        self._closed = False
        logger.info(
            "HyperSearch initialized — data_dir=%s, model=%s",
            data_path,
            model,
        )

    @property
    def settings(self) -> Settings:
        """Access the underlying Settings object."""
        return self._settings

    def create_collection(self, name: str) -> Collection:
        """Create a new collection and return a ``Collection`` handle.

        Args:
            name: Collection name. Must be lowercase alphanumeric with
                hyphens/underscores (e.g. ``"my-collection"``).

        Returns:
            A :class:`~hypersearch.collection.Collection` ready for ingestion.

        Raises:
            FileExistsError: If the collection already exists.
        """
        self._check_closed()
        from hypersearch.db import _db_path, _init_connection

        db_file = _db_path(self._settings, name)
        if db_file.exists():
            raise FileExistsError(
                f"Collection '{name}' already exists at {db_file}"
            )

        conn = _init_connection(db_file)

        # Register in the connection pool
        from hypersearch.db import _connections, _metadata
        from hypersearch.db import _read_creation_time

        _connections[name] = conn
        _metadata[name] = {"created_at": _read_creation_time(db_file)}

        logger.info("Created collection '%s'", name)
        return Collection(name=name, conn=conn, settings=self._settings, db_path=db_file)

    def get_collection(self, name: str) -> Collection:
        """Open an existing collection.

        Args:
            name: Collection name.

        Returns:
            A :class:`~hypersearch.collection.Collection` handle.

        Raises:
            FileNotFoundError: If the collection does not exist.
        """
        self._check_closed()
        from hypersearch.db import _db_path, connect

        db_file = _db_path(self._settings, name)
        if not db_file.exists():
            raise FileNotFoundError(f"Collection '{name}' not found at {db_file}")

        conn = connect(self._settings, name)
        return Collection(name=name, conn=conn, settings=self._settings, db_path=db_file)

    def list_collections(self) -> list[dict[str, Any]]:
        """List all collections with basic metadata.

        Returns:
            A list of dicts with ``name``, ``row_count``, ``size_bytes``,
            and ``created_at`` fields.
        """
        self._check_closed()
        from hypersearch.db import list_collections

        return list_collections(self._settings)

    def delete_collection(self, name: str) -> None:
        """Delete a collection and its database file.

        Args:
            name: Collection name.

        Raises:
            FileNotFoundError: If the collection does not exist.
        """
        self._check_closed()
        from hypersearch.db import _db_path, drop

        db_file = _db_path(self._settings, name)
        if not db_file.exists():
            raise FileNotFoundError(f"Collection '{name}' not found")

        drop(self._settings, name)
        logger.info("Deleted collection '%s'", name)

    def close(self) -> None:
        """Close all database connections.

        After calling ``close()``, further operations will raise
        ``RuntimeError``.
        """
        if not self._closed:
            from hypersearch.db import disconnect_all

            disconnect_all()
            self._closed = True
            logger.info("HyperSearch closed")

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("HyperSearch client has been closed")

    def __enter__(self) -> HyperSearch:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return (
            f"HyperSearch(data_dir={self._settings.storage.data_dir!r}, "
            f"model={self._settings.embedding.model!r}, "
            f"status={status!r})"
        )
