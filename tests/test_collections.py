"""Comprehensive tests for collection CRUD endpoints."""

from __future__ import annotations


class TestCreateCollection:
    def test_create_returns_201(self, client):
        resp = client.post("/v1/collections", json={"name": "test_col"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test_col"
        assert "created_at" in data
        assert "storage_path" in data

    def test_create_duplicate_returns_409(self, client):
        client.post("/v1/collections", json={"name": "dupe"})
        resp = client.post("/v1/collections", json={"name": "dupe"})
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_create_validates_name_pattern(self, client):
        """Collection names must be lowercase alphanumeric + hyphens/underscores."""
        resp = client.post("/v1/collections", json={"name": "UPPER_CASE"})
        assert resp.status_code == 422

    def test_create_rejects_empty_name(self, client):
        resp = client.post("/v1/collections", json={"name": ""})
        assert resp.status_code == 422

    def test_create_with_description(self, client):
        resp = client.post(
            "/v1/collections",
            json={"name": "described", "description": "A test collection"},
        )
        assert resp.status_code == 201

    def test_create_stores_duckdb_file(self, client, settings):
        client.post("/v1/collections", json={"name": "file_check"})
        db_file = settings.storage.data_dir / "file_check.duckdb"
        assert db_file.exists()


class TestListCollections:
    def test_list_empty(self, client):
        resp = client.get("/v1/collections")
        assert resp.status_code == 200
        assert resp.json()["collections"] == []

    def test_list_multiple(self, client):
        client.post("/v1/collections", json={"name": "alpha"})
        client.post("/v1/collections", json={"name": "beta"})
        resp = client.get("/v1/collections")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()["collections"]]
        assert "alpha" in names
        assert "beta" in names

    def test_list_includes_metadata(self, client):
        client.post("/v1/collections", json={"name": "meta_test"})
        resp = client.get("/v1/collections")
        col = resp.json()["collections"][0]
        assert "row_count" in col
        assert "size_bytes" in col
        assert col["row_count"] == 0  # empty collection


class TestDeleteCollection:
    def test_delete_returns_204(self, client):
        client.post("/v1/collections", json={"name": "to_delete"})
        resp = client.delete("/v1/collections/to_delete")
        assert resp.status_code == 204

    def test_delete_removes_from_listing(self, client):
        client.post("/v1/collections", json={"name": "ephemeral"})
        client.delete("/v1/collections/ephemeral")
        resp = client.get("/v1/collections")
        names = [c["name"] for c in resp.json()["collections"]]
        assert "ephemeral" not in names

    def test_delete_removes_file(self, client, settings):
        client.post("/v1/collections", json={"name": "rm_file"})
        client.delete("/v1/collections/rm_file")
        assert not (settings.storage.data_dir / "rm_file.duckdb").exists()

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/v1/collections/ghost")
        assert resp.status_code == 404

    def test_create_delete_recreate(self, client):
        """Regression: ensure a deleted collection can be recreated."""
        client.post("/v1/collections", json={"name": "phoenix"})
        client.delete("/v1/collections/phoenix")
        resp = client.post("/v1/collections", json={"name": "phoenix"})
        assert resp.status_code == 201
