"""Tests for ingestion endpoint."""

from __future__ import annotations

import csv
import io
import json


def test_ingest_csv_upload(client, settings):
    """Ingest a small CSV file via multipart upload."""
    # Create collection first
    client.post("/v1/collections", json={"name": "products"})

    # Build a tiny CSV in-memory
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["title", "description", "price"])
    writer.writeheader()
    for i in range(5):
        writer.writerow({
            "title": f"Product {i}",
            "description": f"A great product number {i}",
            "price": 10.0 + i,
        })
    csv_bytes = buf.getvalue().encode()

    config = json.dumps({"search_template": "{title} {description}"})

    resp = client.post(
        "/v1/collections/products/ingest",
        files={"file": ("products.csv", csv_bytes, "text/csv")},
        data={"config": config},
    )

    assert resp.status_code == 202
    data = resp.json()
    assert data["rows_queued"] == 5
    assert data["status"] == "completed"


def test_ingest_missing_collection(client):
    """Ingesting into a non-existent collection should 404."""
    buf = b"col1,col2\na,b"
    resp = client.post(
        "/v1/collections/ghost/ingest",
        files={"file": ("data.csv", buf, "text/csv")},
    )
    assert resp.status_code == 404
