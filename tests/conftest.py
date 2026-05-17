"""Shared test fixtures."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from hypersearch.config import Settings, get_settings
from hypersearch.server import create_app


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory for tests."""
    data = tmp_path / "data"
    data.mkdir()
    return data


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Return a Settings instance with temporary storage directories."""
    data_dir = tmp_path / "data"
    snap_dir = tmp_path / "snapshots"
    data_dir.mkdir()
    snap_dir.mkdir()

    return Settings(
        storage={"data_dir": str(data_dir), "snapshot_dir": str(snap_dir)},
        embedding={"batch_size": 32},
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    """FastAPI test client wired to temporary storage."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
