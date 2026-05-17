"""BYOD data ingestion — stream CSV/JSON/Parquet, build document column, embed, and store."""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from hypersearch import embeddings
from hypersearch.config import Settings

logger = logging.getLogger(__name__)

HYPER_ID = "_hyper_id_"
HYPER_DOC = "_hyper_document_"
HYPER_VEC = "_hyper_vector_"
RESERVED_COLUMNS = {HYPER_ID, HYPER_DOC, HYPER_VEC}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_file(
    conn: duckdb.DuckDBPyConnection,
    source: Path | io.BytesIO,
    *,
    filename: str,
    settings: Settings,
    search_template: str | None = None,
    metadata_columns: list[str] | None = None,
    batch_size: int | None = None,
) -> int:
    """Read *source*, embed text, and write into the ``documents`` table.

    Returns the number of rows ingested.
    """
    template = search_template or settings.defaults.search_template
    meta_cols = metadata_columns or settings.defaults.metadata_columns
    chunk_size = batch_size or settings.embedding.batch_size

    df = _read_source(source, filename)
    total_rows = len(df)
    logger.info("Read %d rows from '%s' (%d columns)", total_rows, filename, len(df.columns))

    # Derive the document column from the template
    df = _build_document_column(df, template)

    # Resolve metadata columns — default to all non-reserved columns
    user_columns = [c for c in df.columns if c not in RESERVED_COLUMNS]
    if meta_cols:
        missing = set(meta_cols) - set(user_columns)
        if missing:
            raise ValueError(f"Metadata columns not found in dataset: {missing}")
        user_columns = meta_cols

    # Process in chunks to bound memory
    _ensure_table(conn, df, user_columns, settings)

    for offset in range(0, total_rows, chunk_size):
        chunk = df.slice(offset, chunk_size)
        texts = chunk[HYPER_DOC].to_list()

        vectors = embeddings.embed(
            texts,
            model_name=settings.embedding.model,
            device=settings.embedding.device,
            batch_size=chunk_size,
        )

        _insert_chunk(conn, chunk, vectors, user_columns)
        logger.info("Ingested rows %d–%d / %d", offset, min(offset + chunk_size, total_rows), total_rows)

    # Build or rebuild HNSW index
    _build_hnsw_index(conn)

    return total_rows


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def _read_source(source: Path | io.BytesIO, filename: str) -> pl.DataFrame:
    """Dispatch to the correct Polars reader based on file extension."""
    ext = Path(filename).suffix.lower()

    if isinstance(source, Path):
        if ext == ".csv":
            return pl.read_csv(source)
        if ext == ".parquet":
            return pl.read_parquet(source)
        if ext in (".json", ".jsonl", ".ndjson"):
            return pl.read_ndjson(source) if ext in (".jsonl", ".ndjson") else pl.read_json(source)
        raise ValueError(f"Unsupported file extension: {ext}")

    # BytesIO from upload
    data = source.read()
    if ext == ".csv":
        return pl.read_csv(io.BytesIO(data))
    if ext == ".parquet":
        return pl.read_parquet(io.BytesIO(data))
    if ext in (".json", ".jsonl", ".ndjson"):
        return pl.read_ndjson(io.BytesIO(data)) if ext in (".jsonl", ".ndjson") else pl.read_json(io.BytesIO(data))
    raise ValueError(f"Unsupported file extension: {ext}")


# ---------------------------------------------------------------------------
# Document column construction
# ---------------------------------------------------------------------------

def _build_document_column(df: pl.DataFrame, template: str) -> pl.DataFrame:
    """Create ``_hyper_document_`` by formatting *template* against each row."""
    def _fmt(row: dict[str, Any]) -> str:
        return template.format_map({k: (v if v is not None else "") for k, v in row.items()})

    docs = [_fmt(row) for row in df.iter_rows(named=True)]
    return df.with_columns(pl.Series(HYPER_DOC, docs))


# ---------------------------------------------------------------------------
# DuckDB table management
# ---------------------------------------------------------------------------

_DUCKDB_TYPE_MAP: dict[type, str] = {
    pl.Int8: "TINYINT",
    pl.Int16: "SMALLINT",
    pl.Int32: "INTEGER",
    pl.Int64: "BIGINT",
    pl.UInt8: "UTINYINT",
    pl.UInt16: "USMALLINT",
    pl.UInt32: "UINTEGER",
    pl.UInt64: "UBIGINT",
    pl.Float32: "FLOAT",
    pl.Float64: "DOUBLE",
    pl.Boolean: "BOOLEAN",
    pl.Date: "DATE",
    pl.Datetime: "TIMESTAMP",
    pl.Utf8: "VARCHAR",
    pl.String: "VARCHAR",
}


def _polars_to_duckdb_type(dtype: pl.DataType) -> str:
    """Map a Polars dtype to a DuckDB column type string."""
    for pl_type, duck_type in _DUCKDB_TYPE_MAP.items():
        if isinstance(dtype, pl_type):
            return duck_type
    return "VARCHAR"  # safe fallback


def _ensure_table(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
    user_columns: list[str],
    settings: Settings,
) -> None:
    """Create the ``documents`` table if it doesn't exist, matching the inferred schema."""
    from hypersearch.db import table_exists

    if table_exists(conn):
        return  # table already exists; we append

    dim = settings.embedding.dimensions
    col_defs = [f"{HYPER_ID} VARCHAR PRIMARY KEY"]

    for col in user_columns:
        dtype = df[col].dtype
        col_defs.append(f'"{col}" {_polars_to_duckdb_type(dtype)}')

    col_defs.append(f"{HYPER_DOC} VARCHAR")
    col_defs.append(f"{HYPER_VEC} FLOAT[{dim}]")

    ddl = f"CREATE TABLE documents ({', '.join(col_defs)})"
    conn.execute(ddl)
    logger.info("Created documents table: %s", ddl)


def _insert_chunk(
    conn: duckdb.DuckDBPyConnection,
    chunk: pl.DataFrame,
    vectors: np.ndarray,
    user_columns: list[str],
) -> None:
    """Insert a chunk of rows + their embedding vectors into DuckDB."""
    n = len(chunk)
    ids = [str(uuid.uuid4()) for _ in range(n)]
    docs = chunk[HYPER_DOC].to_list()

    # Build per-row tuples: (id, *user_cols, document, vector)
    rows: list[tuple[Any, ...]] = []
    for i in range(n):
        user_vals = tuple(chunk[col][i] for col in user_columns)
        vec = vectors[i].tolist()
        rows.append((ids[i], *user_vals, docs[i], vec))

    placeholders = ", ".join(["?"] * (2 + len(user_columns) + 1))  # id + user + doc + vec
    col_names = ", ".join(
        [HYPER_ID] + [f'"{c}"' for c in user_columns] + [HYPER_DOC, HYPER_VEC]
    )
    sql = f"INSERT INTO documents ({col_names}) VALUES ({placeholders})"

    conn.executemany(sql, rows)


def _build_hnsw_index(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the HNSW index on the vector column. Drops existing index first."""
    try:
        conn.execute("DROP INDEX IF EXISTS idx_hyper_vector")
    except duckdb.Error:
        pass  # index may not exist

    conn.execute(
        f"CREATE INDEX idx_hyper_vector ON documents USING HNSW ({HYPER_VEC}) "
        "WITH (metric = 'cosine')"
    )
    logger.info("HNSW index built on %s", HYPER_VEC)
