"""Shared test fixtures — isolated per-test storage and properly reset state."""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from hypersearch.config import Settings, get_settings
from hypersearch.db import _reset as db_reset
from hypersearch.metrics import _collector


@pytest.fixture(autouse=True)
def _isolate_db_state():
    """Reset the global connection registry between tests."""
    yield
    db_reset()


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
    from hypersearch.server import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Library API fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hs(tmp_path: Path):
    """Create a HyperSearch client with temporary storage."""
    from hypersearch import HyperSearch

    data_dir = tmp_path / "hs_data"
    snap_dir = tmp_path / "hs_snaps"

    hs = HyperSearch(data_dir=data_dir, snapshot_dir=snap_dir, batch_size=32)
    yield hs
    hs.close()


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    """Create a sample CSV file for testing."""
    csv_file = tmp_path / "sample.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["title", "description", "category", "price"]
        )
        writer.writeheader()
        for i in range(20):
            writer.writerow(
                {
                    "title": f"Product {i}",
                    "description": f"A great product number {i} with special features",
                    "category": "outdoor" if i % 2 == 0 else "indoor",
                    "price": str(10.0 + i * 5),
                }
            )
    return csv_file


@pytest.fixture()
def small_csv(tmp_path: Path) -> Path:
    """Create a minimal 3-row CSV for quick tests."""
    csv_file = tmp_path / "small.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label"])
        writer.writeheader()
        writer.writerow({"text": "remote code execution vulnerability", "label": "critical"})
        writer.writerow({"text": "SQL injection in login form", "label": "high"})
        writer.writerow({"text": "low severity info disclosure", "label": "low"})
    return csv_file
