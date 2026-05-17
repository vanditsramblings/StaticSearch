"""Tests for collection CRUD endpoints."""

from __future__ import annotations


def test_create_collection(client):
    resp = client.post("/v1/collections", json={"name": "test_col"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test_col"
    assert "created_at" in data


def test_create_duplicate_collection(client):
    client.post("/v1/collections", json={"name": "dupe"})
    resp = client.post("/v1/collections", json={"name": "dupe"})
    assert resp.status_code == 409


def test_list_collections(client):
    client.post("/v1/collections", json={"name": "alpha"})
    client.post("/v1/collections", json={"name": "beta"})
    resp = client.get("/v1/collections")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["collections"]]
    assert "alpha" in names
    assert "beta" in names


def test_delete_collection(client):
    client.post("/v1/collections", json={"name": "to_delete"})
    resp = client.delete("/v1/collections/to_delete")
    assert resp.status_code == 204

    # Should be gone
    resp = client.get("/v1/collections")
    names = [c["name"] for c in resp.json()["collections"]]
    assert "to_delete" not in names


def test_delete_nonexistent_collection(client):
    resp = client.delete("/v1/collections/ghost")
    assert resp.status_code == 404
