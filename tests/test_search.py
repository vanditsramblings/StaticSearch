"""Comprehensive tests for search endpoint and filter DSL."""

from __future__ import annotations

import csv
import io
import json


def _seed_collection(client, name: str = "searchable", n: int = 10):
    """Create a collection and ingest sample hiking boot data."""
    client.post("/v1/collections", json={"name": name})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["title", "category", "price"])
    writer.writeheader()
    for i in range(n):
        writer.writerow({
            "title": f"Hiking Boot Model {i}",
            "category": "outdoor" if i % 2 == 0 else "indoor",
            "price": str(50.0 + i * 10),
        })
    csv_bytes = buf.getvalue().encode()

    config = json.dumps({
        "search_template": "{title} {category}",
        "metadata_columns": ["title", "category", "price"],
    })

    client.post(
        f"/v1/collections/{name}/ingest",
        files={"file": ("boots.csv", csv_bytes, "text/csv")},
        data={"config": config},
    )


class TestBasicSearch:
    def test_search_returns_results(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "waterproof outdoor boots", "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) <= 5
        assert data["latency_ms"] > 0

    def test_search_result_structure(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "hiking boots", "top_k": 3},
        )
        for result in resp.json()["results"]:
            assert "score" in result
            assert "document" in result
            assert "metadata" in result
            assert isinstance(result["score"], float)
            assert isinstance(result["metadata"], dict)

    def test_search_scores_descending(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "outdoor hiking", "top_k": 10},
        )
        scores = [r["score"] for r in resp.json()["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_search_top_k_limit(self, client, settings):
        _seed_collection(client, n=20)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "boots", "top_k": 3},
        )
        assert len(resp.json()["results"]) == 3


class TestSearchFilters:
    def test_equality_filter(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={
                "query": "hiking boots",
                "top_k": 10,
                "filters": {"category": "outdoor"},
            },
        )
        assert resp.status_code == 200
        for r in resp.json()["results"]:
            assert r["metadata"]["category"] == "outdoor"

    def test_filter_narrows_results(self, client, settings):
        """Filtered results should be a subset of unfiltered."""
        _seed_collection(client, n=20)

        resp_all = client.post(
            "/v1/collections/searchable/search",
            json={"query": "boots", "top_k": 20},
        )
        resp_filtered = client.post(
            "/v1/collections/searchable/search",
            json={"query": "boots", "top_k": 20, "filters": {"category": "indoor"}},
        )
        assert len(resp_filtered.json()["results"]) < len(resp_all.json()["results"])

    def test_invalid_filter_operator(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={
                "query": "boots",
                "filters": {"price": {"$invalid": 50}},
            },
        )
        assert resp.status_code == 400


class TestSearchValidation:
    def test_search_empty_collection_returns_400(self, client):
        client.post("/v1/collections", json={"name": "empty"})
        resp = client.post(
            "/v1/collections/empty/search",
            json={"query": "anything"},
        )
        assert resp.status_code == 400

    def test_search_nonexistent_collection_returns_404(self, client):
        resp = client.post(
            "/v1/collections/ghost/search",
            json={"query": "anything"},
        )
        assert resp.status_code == 404

    def test_search_empty_query_returns_422(self, client, settings):
        _seed_collection(client)
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "", "top_k": 5},
        )
        assert resp.status_code == 422

    def test_search_top_k_too_large_returns_422(self, client, settings):
        resp = client.post(
            "/v1/collections/searchable/search",
            json={"query": "test", "top_k": 9999},
        )
        assert resp.status_code == 422
