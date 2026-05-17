"""Tests for snapshot and restore endpoints."""

from __future__ import annotations

import csv
import io
import json


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


def test_create_snapshot(client, settings):
    """Snapshot should create a file and return metadata."""
    _seed(client)
    resp = client.post("/v1/collections/snap_test/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["size_bytes"] > 0
    assert "snapshot_path" in data


def test_restore_snapshot(client, settings):
    """Restore should swap the live database with the snapshot."""
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


def test_snapshot_nonexistent(client):
    """Snapshot of non-existent collection should 404."""
    resp = client.post("/v1/collections/ghost/snapshot")
    assert resp.status_code == 404
