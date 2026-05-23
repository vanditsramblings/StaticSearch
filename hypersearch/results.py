"""Search result types for the HyperSearch library API.

Provides clean, ergonomic result objects with support for iteration,
indexing, and conversion to Polars DataFrames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single search result with score, document text, and metadata."""

    score: float
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        doc_preview = self.document[:80] + "…" if len(self.document) > 80 else self.document
        return f"SearchHit(score={self.score:.4f}, doc={doc_preview!r})"


@dataclass(frozen=True, slots=True)
class SearchResults:
    """Collection of search results with latency info.

    Supports iteration, indexing, and conversion::

        results = collection.search("remote code execution")
        for hit in results:
            print(hit.score, hit.document)

        # Direct indexing
        best = results[0]

        # Convert to DataFrame
        df = results.to_dataframe()
    """

    hits: list[SearchHit]
    latency_ms: float
    query: str

    def __iter__(self) -> Iterator[SearchHit]:
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    def __getitem__(self, idx: int) -> SearchHit:
        return self.hits[idx]

    def __bool__(self) -> bool:
        return len(self.hits) > 0

    def __repr__(self) -> str:
        return (
            f"SearchResults(query={self.query!r}, "
            f"hits={len(self.hits)}, "
            f"latency={self.latency_ms:.1f}ms)"
        )

    def to_dataframe(self):
        """Convert results to a Polars DataFrame.

        Columns: ``score``, ``document``, plus all metadata keys.

        Returns:
            polars.DataFrame: A DataFrame with one row per hit.

        Raises:
            ImportError: If Polars is not installed (it's a required dep, so unlikely).
        """
        import polars as pl

        if not self.hits:
            return pl.DataFrame({"score": [], "document": []})

        rows = []
        for hit in self.hits:
            row = {"score": hit.score, "document": hit.document}
            row.update(hit.metadata)
            rows.append(row)

        return pl.DataFrame(rows)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Convert results to a list of plain dicts.

        Each dict has ``score``, ``document``, and all metadata keys
        flattened into the top level.
        """
        results = []
        for hit in self.hits:
            row: dict[str, Any] = {"score": hit.score, "document": hit.document}
            row.update(hit.metadata)
            results.append(row)
        return results
