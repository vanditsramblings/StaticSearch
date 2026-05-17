"""Vector search execution — query embedding, SQL generation, filter parsing."""

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
) -> tuple[list[dict[str, Any]], float]:
    """Run a vector similarity search and return ``(results, latency_ms)``.

    Each result dict contains ``score``, ``document``, and ``metadata``.
    """
    t0 = time.perf_counter()

    # 1. Embed the query
    query_vec = embeddings.embed(
        [query],
        model_name=settings.embedding.model,
        device=settings.embedding.device,
    )[0].tolist()

    # 2. Build SQL
    where_clause, where_params = _parse_filters(filters)

    # Retrieve all non-internal columns for metadata
    all_columns = _get_user_columns(conn)

    select_cols = ", ".join([f'"{c}"' for c in all_columns] + [HYPER_DOC])
    distance_expr = f"array_cosine_distance({HYPER_VEC}, ?::FLOAT[{len(query_vec)}])"

    sql = f"SELECT {select_cols}, {distance_expr} AS _distance FROM documents"
    params: list[Any] = [query_vec]

    if where_clause:
        sql += f" WHERE {where_clause}"
        params.extend(where_params)

    sql += " ORDER BY _distance ASC LIMIT ?"
    params.append(top_k)

    # 3. Execute
    rows = conn.execute(sql, params).fetchall()
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


def _get_user_columns(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return column names from the documents table, excluding internal columns."""
    from hypersearch.ingest import HYPER_ID, RESERVED_COLUMNS

    info = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'documents' ORDER BY ordinal_position"
    ).fetchall()

    return [row[0] for row in info if row[0] not in RESERVED_COLUMNS]
