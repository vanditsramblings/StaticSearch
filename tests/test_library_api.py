"""Tests for the HyperSearch library API — the primary programmatic interface.

Covers HyperSearch client, Collection lifecycle, SearchResults, and error handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# HyperSearch client
# ---------------------------------------------------------------------------


class TestHyperSearchClient:
    """Test the top-level HyperSearch class."""

    def test_create_with_defaults(self, tmp_path):
        from hypersearch import HyperSearch

        hs = HyperSearch(data_dir=tmp_path / "data")
        assert not hs._closed
        assert (tmp_path / "data").exists()
        hs.close()

    def test_context_manager(self, tmp_path):
        from hypersearch import HyperSearch

        with HyperSearch(data_dir=tmp_path / "data") as hs:
            assert not hs._closed
        assert hs._closed

    def test_operations_after_close_raise(self, tmp_path):
        from hypersearch import HyperSearch

        hs = HyperSearch(data_dir=tmp_path / "data")
        hs.close()
        with pytest.raises(RuntimeError, match="closed"):
            hs.create_collection("test")

    def test_repr(self, hs):
        r = repr(hs)
        assert "HyperSearch" in r
        assert "all-MiniLM-L6-v2" in r
        assert "open" in r

    def test_list_empty(self, hs):
        result = hs.list_collections()
        assert result == []


class TestCollectionCRUD:
    """Test collection create / get / delete lifecycle."""

    def test_create_collection(self, hs):
        col = hs.create_collection("test-col")
        assert col.name == "test-col"
        assert col.row_count == 0

    def test_create_duplicate_raises(self, hs):
        hs.create_collection("dup")
        with pytest.raises(FileExistsError, match="already exists"):
            hs.create_collection("dup")

    def test_get_collection(self, hs, small_csv):
        col = hs.create_collection("get-test")
        col.ingest(small_csv)

        col2 = hs.get_collection("get-test")
        assert col2.name == "get-test"
        assert col2.row_count == 3

    def test_get_nonexistent_raises(self, hs):
        with pytest.raises(FileNotFoundError):
            hs.get_collection("ghost")

    def test_delete_collection(self, hs):
        hs.create_collection("to-delete")
        hs.delete_collection("to-delete")
        with pytest.raises(FileNotFoundError):
            hs.get_collection("to-delete")

    def test_delete_nonexistent_raises(self, hs):
        with pytest.raises(FileNotFoundError):
            hs.delete_collection("ghost")

    def test_list_collections_after_create(self, hs):
        hs.create_collection("alpha")
        hs.create_collection("beta")
        cols = hs.list_collections()
        names = [c["name"] for c in cols]
        assert "alpha" in names
        assert "beta" in names


# ---------------------------------------------------------------------------
# Collection.ingest()
# ---------------------------------------------------------------------------


class TestCollectionIngest:
    """Test data ingestion through the library API."""

    def test_ingest_csv(self, hs, sample_csv):
        col = hs.create_collection("ingest-test")
        rows = col.ingest(sample_csv, template="{title} {description}")
        assert rows == 20
        assert col.row_count == 20

    def test_ingest_with_metadata_columns(self, hs, sample_csv):
        col = hs.create_collection("meta-test")
        rows = col.ingest(
            sample_csv,
            template="{title} {description}",
            metadata_columns=["title", "category", "price"],
        )
        assert rows == 20

    def test_ingest_missing_file_raises(self, hs):
        col = hs.create_collection("bad-file")
        with pytest.raises(FileNotFoundError):
            col.ingest("/nonexistent/file.csv")

    def test_ingest_invalid_metadata_raises(self, hs, sample_csv):
        col = hs.create_collection("bad-meta")
        with pytest.raises(ValueError, match="not found"):
            col.ingest(
                sample_csv,
                template="{title} {description}",
                metadata_columns=["nonexistent_column"],
            )

    def test_collection_info_after_ingest(self, hs, sample_csv):
        col = hs.create_collection("info-test")
        col.ingest(sample_csv, template="{title} {description}")
        info = col.info
        assert info["name"] == "info-test"
        assert info["row_count"] == 20
        assert info["db_size_bytes"] > 0
        assert "columns" in info


# ---------------------------------------------------------------------------
# Collection.search()
# ---------------------------------------------------------------------------


class TestCollectionSearch:
    """Test search through the library API."""

    @pytest.fixture(autouse=True)
    def _setup(self, hs, sample_csv):
        self.col = hs.create_collection("search-test")
        self.col.ingest(
            sample_csv,
            template="{title} {description}",
            metadata_columns=["title", "category", "price"],
        )

    def test_basic_search(self):
        results = self.col.search("product with special features")
        assert len(results) > 0
        assert results.latency_ms > 0
        assert results.query == "product with special features"

    def test_search_top_k(self):
        results = self.col.search("product", top_k=3)
        assert len(results) <= 3

    def test_search_scores_descending(self):
        results = self.col.search("product", top_k=10)
        scores = [hit.score for hit in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_hit_structure(self):
        results = self.col.search("product", top_k=1)
        hit = results[0]
        assert isinstance(hit.score, float)
        assert isinstance(hit.document, str)
        assert isinstance(hit.metadata, dict)

    def test_search_with_equality_filter(self):
        results = self.col.search(
            "product", top_k=20, filters={"category": "outdoor"}
        )
        for hit in results:
            assert hit.metadata["category"] == "outdoor"

    def test_search_empty_collection_raises(self, hs):
        empty = hs.create_collection("empty-search")
        with pytest.raises(RuntimeError, match="empty"):
            empty.search("anything")

    def test_search_invalid_filter_raises(self):
        with pytest.raises(ValueError, match="Unknown filter operator"):
            self.col.search("test", filters={"price": {"$regex": ".*"}})


# ---------------------------------------------------------------------------
# SearchResults
# ---------------------------------------------------------------------------


class TestSearchResults:
    """Test SearchResults / SearchHit ergonomics."""

    @pytest.fixture(autouse=True)
    def _setup(self, hs, sample_csv):
        col = hs.create_collection("results-test")
        col.ingest(sample_csv, template="{title} {description}")
        self.results = col.search("product features", top_k=5)

    def test_iteration(self):
        hits = list(self.results)
        assert len(hits) == len(self.results)

    def test_indexing(self):
        first = self.results[0]
        assert first.score == self.results.hits[0].score

    def test_bool(self):
        assert bool(self.results)

    def test_repr(self):
        r = repr(self.results)
        assert "SearchResults" in r
        assert "hits=" in r

    def test_hit_repr(self):
        r = repr(self.results[0])
        assert "SearchHit" in r

    def test_to_dataframe(self):
        df = self.results.to_dataframe()
        assert len(df) == len(self.results)
        assert "score" in df.columns
        assert "document" in df.columns

    def test_to_dicts(self):
        dicts = self.results.to_dicts()
        assert isinstance(dicts, list)
        assert len(dicts) == len(self.results)
        assert "score" in dicts[0]
        assert "document" in dicts[0]

    def test_empty_results_to_dataframe(self):
        from hypersearch.results import SearchResults

        empty = SearchResults(hits=[], latency_ms=0, query="empty")
        df = empty.to_dataframe()
        assert len(df) == 0


# ---------------------------------------------------------------------------
# Collection.snapshot() / restore()
# ---------------------------------------------------------------------------


class TestCollectionSnapshotRestore:
    """Test snapshot and restore through the library API."""

    def test_snapshot_creates_file(self, hs, small_csv):
        col = hs.create_collection("snap-lib")
        col.ingest(small_csv)
        snap_path = col.snapshot()
        assert snap_path.exists()
        assert snap_path.stat().st_size > 0

    def test_snapshot_to_custom_dir(self, hs, small_csv, tmp_path):
        col = hs.create_collection("snap-custom")
        col.ingest(small_csv)
        custom_dir = tmp_path / "custom_snaps"
        snap_path = col.snapshot(dest_dir=custom_dir)
        assert snap_path.exists()
        assert custom_dir in snap_path.parents

    def test_restore_preserves_data(self, hs, small_csv):
        col = hs.create_collection("snap-restore")
        col.ingest(small_csv)
        assert col.row_count == 3

        # Snapshot
        snap_path = col.snapshot()

        # Delete and recreate
        hs.delete_collection("snap-restore")
        col2 = hs.create_collection("snap-restore")
        assert col2.row_count == 0

        # Restore
        col2.restore(snap_path)
        assert col2.row_count == 3

        # Search should work
        results = col2.search("code execution")
        assert len(results) > 0
