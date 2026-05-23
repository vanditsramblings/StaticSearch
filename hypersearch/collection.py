"""Per-collection operations — ingest, search, reindex, snapshot, restore.

This module provides the ``Collection`` class, the primary interface for
interacting with a single HyperSearch collection.  It is created by
:class:`~hypersearch.client.HyperSearch` and should not be instantiated
directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

from hypersearch.config import Settings
from hypersearch.results import SearchHit, SearchResults

logger = logging.getLogger(__name__)


class Collection:
    """A single HyperSearch collection backed by a DuckDB database.

    Provides the full lifecycle: ingest → search → reindex → snapshot.

    Example::

        hs = HyperSearch(data_dir="./data")
        col = hs.create_collection("products")

        # Ingest data
        col.ingest("products.csv", template="{name} {description}")

        # Search
        for hit in col.search("waterproof boots"):
            print(hit.score, hit.document)

        # Re-embed with a different template
        col.reindex(template="{name} {category} {description}")
    """

    def __init__(
        self,
        name: str,
        conn: duckdb.DuckDBPyConnection,
        settings: Settings,
        db_path: Path,
    ) -> None:
        self._name = name
        self._conn = conn
        self._settings = settings
        self._db_path = db_path

    @property
    def name(self) -> str:
        """Collection name."""
        return self._name

    @property
    def row_count(self) -> int:
        """Number of rows in the collection."""
        from hypersearch.db import table_exists

        if not table_exists(self._conn):
            return 0
        result = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return result[0] if result else 0

    @property
    def db_path(self) -> Path:
        """Path to the DuckDB database file."""
        return self._db_path

    @property
    def info(self) -> dict[str, Any]:
        """Collection metadata: name, row_count, db_size, columns."""
        from hypersearch.db import table_exists

        result: dict[str, Any] = {
            "name": self._name,
            "row_count": self.row_count,
            "db_size_bytes": self._db_path.stat().st_size if self._db_path.exists() else 0,
        }

        if table_exists(self._conn):
            from hypersearch.engine import _get_user_columns

            result["columns"] = _get_user_columns(self._conn)

        return result

    def ingest(
        self,
        source: str | Path,
        *,
        template: str | None = None,
        metadata_columns: list[str] | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Ingest a CSV, JSON, or Parquet file into this collection.

        Args:
            source: Path to the data file.
            template: Document construction template, e.g. ``"{title} {desc}"``.
                Defaults to the config's ``defaults.search_template``.
            metadata_columns: Columns to store as filterable metadata.
                Defaults to all columns.
            batch_size: Embedding batch size. Defaults to config value.

        Returns:
            Number of rows ingested.

        Raises:
            FileNotFoundError: If *source* does not exist.
            ValueError: If metadata columns reference missing columns.
        """
        from hypersearch.db import invalidate_column_cache
        from hypersearch.ingest import ingest_file

        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Data file not found: {source}")

        rows = ingest_file(
            self._conn,
            source_path,
            filename=source_path.name,
            settings=self._settings,
            search_template=template,
            metadata_columns=metadata_columns,
            batch_size=batch_size,
        )

        invalidate_column_cache(self._name)
        logger.info("Ingested %d rows into '%s'", rows, self._name)
        return rows

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchResults:
        """Run a semantic search query.

        Args:
            query: Natural language search text.
            top_k: Maximum number of results (1–1000).
            filters: Optional metadata filters. Supports operators:
                ``$eq``, ``$neq``, ``$gt``, ``$gte``, ``$lt``, ``$lte``,
                ``$in``, ``$between``.

        Returns:
            :class:`~hypersearch.results.SearchResults` with scored hits.

        Raises:
            RuntimeError: If the collection is empty (no data ingested).
            ValueError: If an invalid filter operator is used.

        Example::

            results = col.search(
                "remote code execution",
                top_k=5,
                filters={"severity": "CRITICAL", "cvss_score": {"$gte": 9.0}},
            )
            for hit in results:
                print(f"{hit.score:.4f}  {hit.document[:80]}")
        """
        from hypersearch.db import table_exists
        from hypersearch.engine import search as run_search

        if not table_exists(self._conn):
            raise RuntimeError(
                f"Collection '{self._name}' is empty — ingest data first"
            )

        raw_results, latency_ms = run_search(
            self._conn,
            query,
            settings=self._settings,
            top_k=top_k,
            filters=filters,
            collection_name=self._name,
        )

        hits = [
            SearchHit(
                score=r["score"],
                document=r["document"],
                metadata=r["metadata"],
            )
            for r in raw_results
        ]

        return SearchResults(hits=hits, latency_ms=latency_ms, query=query)

    def reindex(
        self,
        *,
        template: str | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Re-embed all documents and rebuild the HNSW index.

        Use this after changing the embedding model or document template.

        Args:
            template: New document construction template. If ``None``,
                re-embeds using the existing ``_hyper_document_`` column.
            batch_size: Embedding batch size override.

        Returns:
            Number of rows re-embedded.
        """
        from hypersearch import embeddings
        from hypersearch.db import invalidate_column_cache, table_exists
        from hypersearch.ingest import HYPER_DOC, HYPER_VEC, _build_hnsw_index

        if not table_exists(self._conn):
            raise RuntimeError(
                f"Collection '{self._name}' is empty — nothing to reindex"
            )

        chunk_size = batch_size or self._settings.embedding.batch_size

        # If a new template is provided, rebuild the document column
        if template:
            import polars as pl
            import re

            fields = re.findall(r"\{(\w+)\}", template)
            if fields:
                from hypersearch.engine import _get_user_columns

                user_cols = _get_user_columns(self._conn)
                available = set(user_cols)
                missing = [f for f in fields if f not in available]
                if missing:
                    raise ValueError(
                        f"Template references columns not in collection: {missing}"
                    )

                # Rebuild _hyper_document_ column
                select_expr = " || ".join(
                    [
                        f"COALESCE(CAST(\"{f}\" AS VARCHAR), '')"
                        if i == 0
                        else f"' ' || COALESCE(CAST(\"{f}\" AS VARCHAR), '')"
                        for i, f in enumerate(fields)
                    ]
                )
                self._conn.execute(
                    f"UPDATE documents SET {HYPER_DOC} = {select_expr}"
                )

        # Re-embed all documents in batches
        total = self.row_count
        logger.info("Re-embedding %d rows in '%s'…", total, self._name)

        # Drop existing index
        try:
            self._conn.execute("DROP INDEX IF EXISTS idx_hyper_vector")
        except duckdb.Error:
            pass

        offset = 0
        processed = 0
        while offset < total:
            rows = self._conn.execute(
                f"SELECT rowid, {HYPER_DOC} FROM documents "
                f"ORDER BY rowid LIMIT {chunk_size} OFFSET {offset}"
            ).fetchall()

            if not rows:
                break

            texts = [r[1] for r in rows]
            rowids = [r[0] for r in rows]

            vectors = embeddings.embed(
                texts,
                model_name=self._settings.embedding.model,
                device=self._settings.embedding.device,
                batch_size=chunk_size,
            )

            for i, rowid in enumerate(rowids):
                vec_list = vectors[i].tolist()
                vec_literal = "[" + ",".join(f"{x:.8f}" for x in vec_list) + "]"
                dim = len(vec_list)
                self._conn.execute(
                    f"UPDATE documents SET {HYPER_VEC} = "
                    f"'{vec_literal}'::FLOAT[{dim}] WHERE rowid = {rowid}"
                )

            processed += len(rows)
            offset += chunk_size
            logger.info("Re-embedded %d / %d rows", processed, total)

        # Rebuild HNSW index
        _build_hnsw_index(self._conn, self._settings)
        invalidate_column_cache(self._name)

        logger.info("Reindex complete: %d rows in '%s'", processed, self._name)
        return processed

    def snapshot(self, dest_dir: str | Path | None = None) -> Path:
        """Create a backup snapshot of this collection.

        Args:
            dest_dir: Directory to save the snapshot. Defaults to the
                configured ``snapshot_dir``.

        Returns:
            Path to the created snapshot file.
        """
        from hypersearch.snapshot import create_snapshot

        if dest_dir:
            # Temporarily override snapshot dir
            import copy

            settings = copy.deepcopy(self._settings)
            settings.storage.snapshot_dir = Path(dest_dir)
            settings.storage.snapshot_dir.mkdir(parents=True, exist_ok=True)
        else:
            settings = self._settings

        result = create_snapshot(settings, self._name)
        return Path(result["snapshot_path"])

    def restore(self, snapshot_path: str | Path) -> None:
        """Restore this collection from a snapshot file.

        Args:
            snapshot_path: Path to the snapshot ``.duckdb`` file.

        Raises:
            FileNotFoundError: If the snapshot file does not exist.
        """
        from hypersearch.db import invalidate_column_cache
        from hypersearch.snapshot import restore_snapshot

        restore_snapshot(self._settings, self._name, str(snapshot_path))
        invalidate_column_cache(self._name)

        # Refresh our connection reference
        from hypersearch.db import connect

        self._conn = connect(self._settings, self._name)
        logger.info("Restored '%s' from %s", self._name, snapshot_path)
