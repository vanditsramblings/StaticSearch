"""BYOD data ingestion — stream CSV/JSON/Parquet, build document column, embed, and store.

Performance-optimized: uses Polars vectorized ops, Arrow bulk insertion,
and tunable HNSW index parameters.
"""

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

    # Derive the document column from the template (vectorized)
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

        _insert_chunk_bulk(conn, chunk, vectors, user_columns)
        logger.info("Ingested rows %d–%d / %d", offset, min(offset + chunk_size, total_rows), total_rows)

    # Build or rebuild HNSW index with tuned parameters
    _build_hnsw_index(conn, settings)

    return total_rows


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def _read_source(source: Path | io.BytesIO, filename: str) -> pl.DataFrame:
    """Dispatch to the correct Polars reader based on file extension."""
    ext = Path(filename).suffix.lower()

    if isinstance(source, Path):
        if ext == ".csv":
            return pl.read_csv(source, infer_schema_length=10000)
        if ext == ".parquet":
            return pl.read_parquet(source)
        if ext in (".json", ".jsonl", ".ndjson"):
            return pl.read_ndjson(source) if ext in (".jsonl", ".ndjson") else pl.read_json(source)
        raise ValueError(f"Unsupported file extension: {ext}")

    # BytesIO from upload
    data = source.read()
    if ext == ".csv":
        return pl.read_csv(io.BytesIO(data), infer_schema_length=10000)
    if ext == ".parquet":
        return pl.read_parquet(io.BytesIO(data))
    if ext in (".json", ".jsonl", ".ndjson"):
        return pl.read_ndjson(io.BytesIO(data)) if ext in (".jsonl", ".ndjson") else pl.read_json(io.BytesIO(data))
    raise ValueError(f"Unsupported file extension: {ext}")


# ---------------------------------------------------------------------------
# Document column construction — vectorized with Polars
# ---------------------------------------------------------------------------

def _build_document_column(df: pl.DataFrame, template: str) -> pl.DataFrame:
    """Create ``_hyper_document_`` using vectorized Polars string concatenation.

    Falls back to row-by-row only if the template uses complex formatting.
    """
    import re

    # Extract field names from template like "{cve} {description}"
    fields = re.findall(r"\{(\w+)\}", template)

    if not fields:
        # No template fields — just use a constant
        return df.with_columns(pl.lit(template).alias(HYPER_DOC))

    # Check if all fields exist in the DataFrame
    available = set(df.columns)
    if all(f in available for f in fields):
        # Build the expression using Polars concat_str
        # Split template into parts around the {field} placeholders
        parts = re.split(r"\{\w+\}", template)
        exprs = []

        for i, field in enumerate(fields):
            if parts[i]:  # Add literal prefix
                exprs.append(pl.lit(parts[i]))
            exprs.append(pl.col(field).cast(pl.Utf8).fill_null(pl.lit("")))

        # Add trailing literal if any
        if parts[-1]:
            exprs.append(pl.lit(parts[-1]))

        if len(exprs) == 1:
            doc_expr = exprs[0]
        else:
            doc_expr = pl.concat_str(exprs)

        return df.with_columns(doc_expr.alias(HYPER_DOC))

    # Fallback: row-by-row (for complex templates)
    logger.warning("Using row-by-row document construction (slow path)")

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


def _insert_chunk_bulk(
    conn: duckdb.DuckDBPyConnection,
    chunk: pl.DataFrame,
    vectors: np.ndarray,
    user_columns: list[str],
) -> None:
    """Insert a chunk using DuckDB's native Polars/Arrow integration (bulk).

    This is 10-50x faster than row-by-row executemany.
    """
    n = len(chunk)

    # Generate UUIDs in batch
    ids = [str(uuid.uuid4()) for _ in range(n)]

    # Build a Polars DataFrame with exactly the columns we need
    insert_df = pl.DataFrame({HYPER_ID: ids})

    # Add user columns
    for col in user_columns:
        insert_df = insert_df.with_columns(chunk[col].alias(col))

    # Add document column
    insert_df = insert_df.with_columns(chunk[HYPER_DOC].alias(HYPER_DOC))

    # Add vector column as list of lists
    vec_lists = [vectors[i].tolist() for i in range(n)]
    insert_df = insert_df.with_columns(pl.Series(HYPER_VEC, vec_lists))

    # Use DuckDB's native Polars integration for bulk insert
    col_names = ", ".join(
        [HYPER_ID] + [f'"{c}"' for c in user_columns] + [HYPER_DOC, HYPER_VEC]
    )

    # Register the DataFrame as a temporary view and INSERT FROM SELECT
    conn.register("_tmp_insert_df", insert_df.to_arrow())
    try:
        conn.execute(f"INSERT INTO documents ({col_names}) SELECT * FROM _tmp_insert_df")
    finally:
        conn.unregister("_tmp_insert_df")


def _build_hnsw_index(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Create the HNSW index on the vector column with tuned parameters.

    Drops existing index first.
    """
    try:
        conn.execute("DROP INDEX IF EXISTS idx_hyper_vector")
    except duckdb.Error:
        pass  # index may not exist

    # Get HNSW parameters from settings or use sensible defaults
    if settings is not None:
        m = settings.embedding.hnsw_m
        ef_construction = settings.embedding.hnsw_ef_construction
    else:
        m = 48
        ef_construction = 256

    conn.execute(
        f"CREATE INDEX idx_hyper_vector ON documents USING HNSW ({HYPER_VEC}) "
        f"WITH (metric = 'cosine', m = {m}, ef_construction = {ef_construction})"
    )
    logger.info(
        "HNSW index built on %s (M=%d, ef_construction=%d)",
        HYPER_VEC, m, ef_construction,
    )
