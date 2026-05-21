"""Comprehensive tests for ingestion endpoint and pipeline."""

from __future__ import annotations

import csv
import io
import json


def _make_csv(rows: list[dict], fieldnames: list[str] | None = None) -> bytes:
    """Helper: create CSV bytes from a list of dicts."""
    if not fieldnames:
        fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


class TestIngestCSV:
    def test_ingest_returns_202(self, client, settings):
        client.post("/v1/collections", json={"name": "products"})

        data = [
            {"title": f"Product {i}", "description": f"Desc {i}", "price": str(10 + i)}
            for i in range(5)
        ]
        csv_bytes = _make_csv(data)
        config = json.dumps({"search_template": "{title} {description}"})

        resp = client.post(
            "/v1/collections/products/ingest",
            files={"file": ("products.csv", csv_bytes, "text/csv")},
            data={"config": config},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["rows_queued"] == 5
        assert body["status"] == "completed"
        assert "task_id" in body

    def test_ingest_updates_row_count(self, client, settings):
        client.post("/v1/collections", json={"name": "counted"})

        data = [{"text": f"Row {i}"} for i in range(3)]
        csv_bytes = _make_csv(data)

        client.post(
            "/v1/collections/counted/ingest",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
        )

        resp = client.get("/v1/collections")
        col = next(c for c in resp.json()["collections"] if c["name"] == "counted")
        assert col["row_count"] == 3

    def test_ingest_with_metadata_columns(self, client, settings):
        """Specifying metadata_columns should limit stored metadata."""
        client.post("/v1/collections", json={"name": "meta_ingest"})

        data = [
            {"title": "Boot", "category": "outdoor", "price": "50", "internal_id": "x123"}
            for _ in range(3)
        ]
        csv_bytes = _make_csv(data)
        config = json.dumps({
            "search_template": "{title} {category}",
            "metadata_columns": ["title", "category", "price"],
        })

        resp = client.post(
            "/v1/collections/meta_ingest/ingest",
            files={"file": ("boots.csv", csv_bytes, "text/csv")},
            data={"config": config},
        )
        assert resp.status_code == 202


class TestIngestValidation:
    def test_ingest_missing_collection_returns_404(self, client):
        csv_bytes = b"col1,col2\na,b"
        resp = client.post(
            "/v1/collections/ghost/ingest",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 404

    def test_ingest_no_file_or_path_returns_400(self, client):
        client.post("/v1/collections", json={"name": "empty_req"})
        resp = client.post("/v1/collections/empty_req/ingest")
        assert resp.status_code in (400, 422)

    def test_ingest_invalid_metadata_column(self, client, settings):
        """Requesting a metadata column that doesn't exist should fail."""
        client.post("/v1/collections", json={"name": "bad_meta"})

        data = [{"text": "hello"}]
        csv_bytes = _make_csv(data)
        config = json.dumps({"metadata_columns": ["nonexistent"]})

        resp = client.post(
            "/v1/collections/bad_meta/ingest",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
            data={"config": config},
        )
        assert resp.status_code == 400


class TestIngestFilePath:
    def test_ingest_server_side_path(self, client, settings, tmp_path):
        """Ingesting via file_path instead of upload."""
        client.post("/v1/collections", json={"name": "local_ingest"})

        # Write CSV to temp directory
        csv_file = tmp_path / "local_data.csv"
        with open(csv_file, "w") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "label"])
            writer.writeheader()
            for i in range(5):
                writer.writerow({"text": f"Document {i}", "label": f"cat_{i % 2}"})

        config = json.dumps({"file_path": str(csv_file)})

        resp = client.post(
            "/v1/collections/local_ingest/ingest",
            data={"config": config},
        )
        assert resp.status_code == 202
        assert resp.json()["rows_queued"] == 5

    def test_ingest_nonexistent_path_returns_400(self, client, settings):
        client.post("/v1/collections", json={"name": "bad_path"})

        config = json.dumps({"file_path": "/nonexistent/file.csv"})
        resp = client.post(
            "/v1/collections/bad_path/ingest",
            data={"config": config},
        )
        assert resp.status_code == 400
