"""Tests for search endpoint."""

from __future__ import annotations

import csv
import io
import json


def _seed_collection(client, name: str = "searchable", n: int = 10):
    """Helper: create a collection and ingest sample data."""
    client.post("/v1/collections", json={"name": name})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["title", "category", "price"])
    writer.writeheader()
    for i in range(n):
        writer.writerow({
            "title": f"Hiking Boot Model {i}",
            "category": "outdoor" if i % 2 == 0 else "indoor",
            "price": 50.0 + i * 10,
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


def test_basic_search(client, settings):
    """Search should return results ranked by similarity."""
    _seed_collection(client)

    resp = client.post(
        "/v1/collections/searchable/search",
        json={"query": "waterproof outdoor boots", "top_k": 5},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) <= 5
    assert data["latency_ms"] > 0

    # Each result should have score, document, and metadata
    for r in data["results"]:
        assert "score" in r
        assert "document" in r
        assert "metadata" in r


def test_search_with_filter(client, settings):
    """Metadata filters should narrow results."""
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


def test_search_empty_collection(client):
    """Searching an empty collection should return 400."""
    client.post("/v1/collections", json={"name": "empty"})
    resp = client.post(
        "/v1/collections/empty/search",
        json={"query": "anything"},
    )
    assert resp.status_code == 400
