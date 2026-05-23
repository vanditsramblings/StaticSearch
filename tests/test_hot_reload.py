"""Tests for the hot-reload staging mechanism."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


class TestStagingLifecycle:
    """Test the staging file prepare / promote / cleanup flow."""

    def test_prepare_staging_creates_file(self, settings):
        from hypersearch.db import connect
        from hypersearch.hot_reload import staging_path, prepare_staging

        connect(settings, "stage_test")
        stage = prepare_staging(settings, "stage_test")

        assert stage.exists()
        assert stage == staging_path(settings, "stage_test")

    def test_cleanup_staging_removes_file(self, settings):
        from hypersearch.db import connect
        from hypersearch.hot_reload import cleanup_staging, prepare_staging, staging_path

        connect(settings, "cleanup_test")
        prepare_staging(settings, "cleanup_test")
        assert staging_path(settings, "cleanup_test").exists()

        cleanup_staging(settings, "cleanup_test")
        assert not staging_path(settings, "cleanup_test").exists()

    def test_cleanup_nonexistent_is_noop(self, settings):
        from hypersearch.hot_reload import cleanup_staging

        # Should not raise
        cleanup_staging(settings, "nonexistent")


class TestRunStagedIngest:
    """Test the full staged ingest workflow."""

    def test_staged_ingest_flow(self, settings, tmp_path):
        from hypersearch.db import connect, table_exists
        from hypersearch.hot_reload import run_staged_ingest

        # Create collection
        connect(settings, "staged_flow")

        # Create CSV
        csv_file = tmp_path / "data.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "label"])
            writer.writeheader()
            for i in range(5):
                writer.writerow({"text": f"Document {i}", "label": f"cat_{i % 2}"})

        task_id, rows = run_staged_ingest(
            settings,
            "staged_flow",
            csv_file,
            "data.csv",
        )

        assert rows == 5
        assert task_id  # non-empty string

        # Verify data is accessible
        conn = connect(settings, "staged_flow")
        assert table_exists(conn)
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 5

    def test_staged_ingest_with_template(self, settings, tmp_path):
        from hypersearch.db import connect
        from hypersearch.hot_reload import run_staged_ingest

        connect(settings, "template_test")

        csv_file = tmp_path / "products.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "desc", "price"])
            writer.writeheader()
            writer.writerow({"name": "Boot", "desc": "Waterproof", "price": "50"})
            writer.writerow({"name": "Shoe", "desc": "Comfortable", "price": "30"})

        task_id, rows = run_staged_ingest(
            settings,
            "template_test",
            csv_file,
            "products.csv",
            search_template="{name} {desc}",
            metadata_columns=["name", "price"],
        )

        assert rows == 2

    def test_staged_ingest_error_cleanup(self, settings, tmp_path):
        from hypersearch.db import connect
        from hypersearch.hot_reload import run_staged_ingest, staging_path

        connect(settings, "error_test")

        # Try to ingest a non-CSV file
        bad_file = tmp_path / "bad.xyz"
        bad_file.write_text("not a valid file")

        with pytest.raises(ValueError, match="Unsupported"):
            run_staged_ingest(
                settings, "error_test", bad_file, "bad.xyz"
            )

        # Staging file should be cleaned up
        assert not staging_path(settings, "error_test").exists()
