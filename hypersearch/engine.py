"""Vector search execution — query embedding, SQL generation, filter parsing.

Optimized for typeahead search with:
- LRU-cached query embeddings (via embeddings.embed_query)
- Cached column schema (avoid per-query information_schema lookup)
- Tunable HNSW ef_search parameter
"""

from __future__ import annotations

import logging
import time
from typing import Any

import duckdb

from hypersearch import embeddings
from hypersearch.config import Settings
from hypersearch.ingest import HYPER_DOC, HYPER_VEC

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filter DSL → SQL WHERE clause
# ---------------------------------------------------------------------------

_OP_MAP = {
    "$eq": "=",
    "$neq": "!=",
    "$gt": ">",
    "$gte": ">=",
    "$lt": "<",
    "$lte": "<=",
}


def _parse_filters(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    """Convert a filter dict into a SQL WHERE fragment and parameter list.

    Supports:
        - Simple equality:  ``{"color": "red"}``
        - Rich operators:   ``{"price": {"$lt": 100}}``
        - ``$in``:          ``{"tags": {"$in": ["a", "b"]}}``
        - ``$between``:     ``{"price": {"$between": [10, 50]}}``
    """
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    for col, condition in filters.items():
        quoted = f'"{col}"'

        if not isinstance(condition, dict):
            # Simple equality
            clauses.append(f"{quoted} = ?")
            params.append(condition)
            continue

        for op, value in condition.items():
            if op in _OP_MAP:
                clauses.append(f"{quoted} {_OP_MAP[op]} ?")
                params.append(value)
            elif op == "$in":
                placeholders = ", ".join(["?"] * len(value))
                clauses.append(f"{quoted} IN ({placeholders})")
                params.extend(value)
            elif op == "$between":
                clauses.append(f"{quoted} BETWEEN ? AND ?")
                params.extend(value[:2])
            else:
                raise ValueError(f"Unknown filter operator: {op}")

    return " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    *,
    settings: Settings,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
    collection_name: str | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Run a vector similarity search and return ``(results, latency_ms)``.

    Each result dict contains ``score``, ``document``, and ``metadata``.
    Uses LRU-cached query embeddings and cached column schema for
    typeahead-grade performance.
    """
    t0 = time.perf_counter()

    # 1. Embed the query (cached for typeahead)
    query_vec = embeddings.embed_query(
        query,
        model_name=settings.embedding.model,
        device=settings.embedding.device,
    ).tolist()

    # 2. Set HNSW ef_search for this query session
    ef_search = settings.embedding.hnsw_ef_search
    try:
        conn.execute(f"SET hnsw_ef_search = {ef_search}")
    except duckdb.Error:
        pass  # older DuckDB versions may not support this

    # 3. Build SQL — use cached column list, INLINE the vector for performance
    #    DuckDB parameter binding for FLOAT[384] arrays is ~110ms due to Python→DuckDB
    #    list conversion. Inlining the vector as a SQL literal brings this to ~3ms.
    where_clause, where_params = _parse_filters(filters)
    all_columns = _get_user_columns_cached(conn, collection_name)

    select_cols = ", ".join([f'"{c}"' for c in all_columns] + [HYPER_DOC])
    dim = len(query_vec)

    # Inline vector as SQL literal — numeric values from our own model, safe from injection
    vec_literal = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
    distance_expr = f"array_cosine_distance({HYPER_VEC}, '{vec_literal}'::FLOAT[{dim}])"

    sql = f"SELECT {select_cols}, {distance_expr} AS _distance FROM documents"
    params: list[Any] = []

    if where_clause:
        sql += f" WHERE {where_clause}"
        params.extend(where_params)

    sql += f" ORDER BY _distance ASC LIMIT {top_k}"

    # 4. Execute
    rows = conn.execute(sql, params).fetchall() if params else conn.execute(sql).fetchall()
    col_names = all_columns + [HYPER_DOC, "_distance"]

    results: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(zip(col_names, row))
        distance = row_dict.pop("_distance")
        document = row_dict.pop(HYPER_DOC)

        results.append({
            "score": round(1.0 - distance, 6),  # cosine similarity = 1 - cosine distance
            "document": document,
            "metadata": row_dict,
        })

    latency_ms = (time.perf_counter() - t0) * 1000
    return results, round(latency_ms, 3)


def _get_user_columns_cached(
    conn: duckdb.DuckDBPyConnection,
    collection_name: str | None = None,
) -> list[str]:
    """Return user columns, using cache if available."""
    from hypersearch.db import get_cached_columns, set_cached_columns

    if collection_name:
        cached = get_cached_columns(collection_name)
        if cached is not None:
            return cached

    # Fall back to schema introspection
    columns = _get_user_columns(conn)

    if collection_name:
        set_cached_columns(collection_name, columns)

    return columns


def _get_user_columns(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return column names from the documents table, excluding internal columns."""
    from hypersearch.ingest import RESERVED_COLUMNS

    info = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'documents' ORDER BY ordinal_position"
    ).fetchall()

    return [row[0] for row in info if row[0] not in RESERVED_COLUMNS]
