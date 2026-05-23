"""Integration tests for the search engine — direct DuckDB + vector queries."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


def _create_and_ingest(tmp_path: Path, n: int = 10):
    """Helper: create a collection and ingest sample data."""
    from hypersearch.config import Settings
    from hypersearch.db import _init_connection
    from hypersearch.ingest import ingest_file

    csv_file = tmp_path / "test_data.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["title", "description", "category", "price"]
        )
        writer.writeheader()
        for i in range(n):
            writer.writerow(
                {
                    "title": f"Widget {i}",
                    "description": f"A sturdy waterproof widget number {i}",
                    "category": "outdoor" if i % 2 == 0 else "indoor",
                    "price": str(10.0 + i * 5),
                }
            )

    db_path = tmp_path / "test.duckdb"
    settings = Settings(
        storage={"data_dir": str(tmp_path), "snapshot_dir": str(tmp_path / "snaps")},
        embedding={"batch_size": 32},
    )
    (tmp_path / "snaps").mkdir(exist_ok=True)

    conn = _init_connection(db_path)
    ingest_file(
        conn,
        csv_file,
        filename="test_data.csv",
        settings=settings,
        search_template="{title} {description}",
        metadata_columns=["title", "category", "price"],
    )
    return conn, settings


class TestSearchDirect:
    """Test the engine.search() function directly (no HTTP server)."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.conn, self.settings = _create_and_ingest(tmp_path, n=15)

    def test_basic_search(self):
        from hypersearch.engine import search

        results, latency = search(
            self.conn, "waterproof widget", settings=self.settings, top_k=5
        )
        assert len(results) == 5
        assert latency > 0

    def test_search_result_keys(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn, "widget", settings=self.settings, top_k=1
        )
        r = results[0]
        assert "score" in r
        assert "document" in r
        assert "metadata" in r

    def test_search_scores_range(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn, "widget", settings=self.settings, top_k=10
        )
        for r in results:
            assert 0.0 <= r["score"] <= 1.0

    def test_search_scores_sorted(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn, "outdoor waterproof", settings=self.settings, top_k=10
        )
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestSearchFilters:
    """Test filter DSL applied to real DuckDB queries."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.conn, self.settings = _create_and_ingest(tmp_path, n=20)

    def test_equality_filter(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"category": "outdoor"},
        )
        for r in results:
            assert r["metadata"]["category"] == "outdoor"

    def test_gt_filter(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"price": {"$gt": 50}},
        )
        for r in results:
            assert float(r["metadata"]["price"]) > 50

    def test_in_filter(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"category": {"$in": ["outdoor"]}},
        )
        for r in results:
            assert r["metadata"]["category"] in ["outdoor"]

    def test_between_filter(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"price": {"$between": [20, 60]}},
        )
        for r in results:
            price = float(r["metadata"]["price"])
            assert 20 <= price <= 60

    def test_combined_filters(self):
        from hypersearch.engine import search

        results, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"category": "outdoor", "price": {"$gte": 30}},
        )
        for r in results:
            assert r["metadata"]["category"] == "outdoor"
            assert float(r["metadata"]["price"]) >= 30

    def test_filter_narrows_results(self):
        from hypersearch.engine import search

        all_results, _ = search(
            self.conn, "widget", settings=self.settings, top_k=20
        )
        filtered, _ = search(
            self.conn,
            "widget",
            settings=self.settings,
            top_k=20,
            filters={"category": "indoor"},
        )
        assert len(filtered) < len(all_results)


class TestColumnCaching:
    """Test the column schema caching mechanism."""

    def test_column_cache(self, tmp_path):
        conn, settings = _create_and_ingest(tmp_path)

        from hypersearch.engine import _get_user_columns_cached

        # First call — schema introspection
        cols1 = _get_user_columns_cached(conn, collection_name="test_cache")
        assert "title" in cols1
        assert "category" in cols1

        # Second call — should use cache
        cols2 = _get_user_columns_cached(conn, collection_name="test_cache")
        assert cols1 == cols2

    def test_cache_invalidation(self, tmp_path):
        conn, settings = _create_and_ingest(tmp_path)
        from hypersearch.db import get_cached_columns, invalidate_column_cache, set_cached_columns

        set_cached_columns("test", ["a", "b"])
        assert get_cached_columns("test") == ["a", "b"]

        invalidate_column_cache("test")
        assert get_cached_columns("test") is None
