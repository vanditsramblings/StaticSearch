"""Unit tests for the filter DSL parser in engine.py."""

from __future__ import annotations

import pytest


class TestFilterParser:
    """Test _parse_filters independently of the database."""

    @pytest.fixture(autouse=True)
    def _import_parser(self):
        from hypersearch.engine import _parse_filters
        self.parse = _parse_filters

    def test_empty_filters(self):
        clause, params = self.parse(None)
        assert clause == ""
        assert params == []

    def test_empty_dict(self):
        clause, params = self.parse({})
        assert clause == ""
        assert params == []

    def test_simple_equality(self):
        clause, params = self.parse({"color": "red"})
        assert '"color" = ?' in clause
        assert params == ["red"]

    def test_multiple_equalities(self):
        clause, params = self.parse({"color": "red", "size": "large"})
        assert '"color" = ?' in clause
        assert '"size" = ?' in clause
        assert " AND " in clause
        assert params == ["red", "large"]

    def test_gt_operator(self):
        clause, params = self.parse({"price": {"$gt": 100}})
        assert '"price" > ?' in clause
        assert params == [100]

    def test_gte_operator(self):
        clause, params = self.parse({"score": {"$gte": 7.5}})
        assert '"score" >= ?' in clause
        assert params == [7.5]

    def test_lt_operator(self):
        clause, params = self.parse({"weight": {"$lt": 50}})
        assert '"weight" < ?' in clause

    def test_lte_operator(self):
        clause, params = self.parse({"rating": {"$lte": 3}})
        assert '"rating" <= ?' in clause

    def test_eq_operator(self):
        clause, params = self.parse({"status": {"$eq": "active"}})
        assert '"status" = ?' in clause
        assert params == ["active"]

    def test_neq_operator(self):
        clause, params = self.parse({"status": {"$neq": "deleted"}})
        assert '"status" != ?' in clause

    def test_in_operator(self):
        clause, params = self.parse({"tag": {"$in": ["a", "b", "c"]}})
        assert '"tag" IN (?, ?, ?)' in clause
        assert params == ["a", "b", "c"]

    def test_between_operator(self):
        clause, params = self.parse({"price": {"$between": [10, 50]}})
        assert '"price" BETWEEN ? AND ?' in clause
        assert params == [10, 50]

    def test_combined_filters(self):
        clause, params = self.parse({
            "category": "shoes",
            "price": {"$lt": 100},
            "color": {"$in": ["red", "blue"]},
        })
        assert " AND " in clause
        assert len(params) == 4  # shoes + 100 + red + blue

    def test_unknown_operator_raises(self):
        with pytest.raises(ValueError, match="Unknown filter operator"):
            self.parse({"x": {"$regex": ".*"}})


class TestConfigModule:
    """Test config loading and defaults."""

    def test_default_settings(self):
        from hypersearch.config import Settings
        s = Settings()
        assert s.server.host == "0.0.0.0"
        assert s.server.port == 8000
        assert s.embedding.model == "all-MiniLM-L6-v2"
        assert s.embedding.dimensions == 384
        assert s.defaults.top_k == 10

    def test_settings_override(self):
        from hypersearch.config import Settings
        s = Settings(server={"port": 9000}, embedding={"batch_size": 64})
        assert s.server.port == 9000
        assert s.embedding.batch_size == 64


class TestMetricsCollector:
    """Test the metrics collection logic."""

    def test_empty_percentiles(self):
        from hypersearch.metrics import MetricsCollector
        mc = MetricsCollector()
        pcts = mc.percentiles()
        assert pcts["p50"] == 0.0
        assert pcts["p99"] == 0.0

    def test_record_and_percentiles(self):
        from hypersearch.metrics import MetricsCollector
        mc = MetricsCollector()
        for i in range(100):
            mc.record_query(float(i))

        pcts = mc.percentiles()
        assert pcts["p50"] == 50.0
        assert pcts["p95"] == 95.0
        assert pcts["p99"] == 99.0
        assert mc.query_count == 100

    def test_uptime(self):
        from hypersearch.metrics import MetricsCollector
        mc = MetricsCollector()
        assert mc.uptime_seconds >= 0

    def test_memory_mb(self):
        from hypersearch.metrics import MetricsCollector
        assert MetricsCollector.memory_mb() > 0

    def test_prometheus_format(self):
        from hypersearch.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_query(10.5)
        output = mc.to_prometheus()
        assert "hypersearch_uptime_seconds" in output
        assert "hypersearch_queries_total 1" in output
        assert "hypersearch_memory_rss_mb" in output


class TestModels:
    """Test Pydantic model validation."""

    def test_collection_name_pattern(self):
        from pydantic import ValidationError
        from hypersearch.models import CreateCollectionRequest

        # Valid names
        CreateCollectionRequest(name="my-collection")
        CreateCollectionRequest(name="test_123")

        # Invalid names
        with pytest.raises(ValidationError):
            CreateCollectionRequest(name="Has Spaces")
        with pytest.raises(ValidationError):
            CreateCollectionRequest(name="UPPERCASE")
        with pytest.raises(ValidationError):
            CreateCollectionRequest(name="")

    def test_search_request_top_k_bounds(self):
        from pydantic import ValidationError
        from hypersearch.models import SearchRequest

        SearchRequest(query="test", top_k=1)
        SearchRequest(query="test", top_k=1000)

        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=0)
        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=1001)

    def test_search_request_empty_query(self):
        from pydantic import ValidationError
        from hypersearch.models import SearchRequest

        with pytest.raises(ValidationError):
            SearchRequest(query="")
