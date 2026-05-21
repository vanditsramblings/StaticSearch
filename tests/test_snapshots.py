"""Comprehensive tests for snapshot and restore endpoints."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path


def _seed(client, name: str = "snap_test"):
    """Create and populate a collection."""
    client.post("/v1/collections", json={"name": name})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["text"])
    writer.writeheader()
    for i in range(3):
        writer.writerow({"text": f"Document {i}"})

    client.post(
        f"/v1/collections/{name}/ingest",
        files={"file": ("docs.csv", buf.getvalue().encode(), "text/csv")},
    )


class TestSnapshot:
    def test_create_snapshot(self, client, settings):
        _seed(client)
        resp = client.post("/v1/collections/snap_test/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["size_bytes"] > 0
        assert "snapshot_path" in data
        assert "created_at" in data

    def test_snapshot_file_exists(self, client, settings):
        _seed(client)
        resp = client.post("/v1/collections/snap_test/snapshot")
        snap_path = resp.json()["snapshot_path"]
        assert Path(snap_path).exists()

    def test_snapshot_nonexistent_returns_404(self, client):
        resp = client.post("/v1/collections/ghost/snapshot")
        assert resp.status_code == 404

    def test_multiple_snapshots(self, client, settings):
        """Multiple snapshots should create distinct files."""
        _seed(client)
        resp1 = client.post("/v1/collections/snap_test/snapshot")
        resp2 = client.post("/v1/collections/snap_test/snapshot")
        assert resp1.json()["snapshot_path"] != resp2.json()["snapshot_path"]


class TestRestore:
    def test_restore_from_snapshot(self, client, settings):
        _seed(client)

        # Take snapshot
        snap_resp = client.post("/v1/collections/snap_test/snapshot")
        snap_path = snap_resp.json()["snapshot_path"]

        # Delete and recreate collection
        client.delete("/v1/collections/snap_test")
        client.post("/v1/collections", json={"name": "snap_test"})

        # Restore
        resp = client.post(
            "/v1/collections/snap_test/restore",
            json={"snapshot_path": snap_path},
        )
        assert resp.status_code == 200
        assert resp.json()["restored"] is True
        assert resp.json()["collection"] == "snap_test"

    def test_restore_preserves_data(self, client, settings):
        """After restore, search should find the original data."""
        _seed(client)

        # Snapshot
        snap_resp = client.post("/v1/collections/snap_test/snapshot")
        snap_path = snap_resp.json()["snapshot_path"]

        # Delete and recreate
        client.delete("/v1/collections/snap_test")
        client.post("/v1/collections", json={"name": "snap_test"})

        # Restore
        client.post(
            "/v1/collections/snap_test/restore",
            json={"snapshot_path": snap_path},
        )

        # Search should work
        resp = client.post(
            "/v1/collections/snap_test/search",
            json={"query": "Document", "top_k": 5},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) > 0

    def test_restore_nonexistent_snapshot_returns_404(self, client, settings):
        client.post("/v1/collections", json={"name": "bad_restore"})
        resp = client.post(
            "/v1/collections/bad_restore/restore",
            json={"snapshot_path": "/nonexistent/file.duckdb"},
        )
        assert resp.status_code == 404


class TestHealthAndMetrics:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "memory_mb" in data
        assert "disk_free_gb" in data
        assert "uptime_seconds" in data
        assert "collections_active" in data

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "hypersearch_uptime_seconds" in resp.text
        assert "hypersearch_queries_total" in resp.text
        assert "hypersearch_memory_rss_mb" in resp.text

    def test_metrics_reflect_queries(self, client, settings):
        """After searches, metrics should show updated query counts."""
        _seed(client, name="metrics_test")

        # Do a few searches
        for _ in range(3):
            client.post(
                "/v1/collections/metrics_test/search",
                json={"query": "test document"},
            )

        resp = client.get("/metrics")
        
        # Parse the metric from the text payload
        queries = 0
        for line in resp.text.splitlines():
            if line.startswith("hypersearch_queries_total"):
                queries = int(line.split()[1])
                break
                
        assert queries >= 3
