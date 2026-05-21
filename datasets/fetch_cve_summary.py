#!/usr/bin/env python3
"""Fetch a lightweight CVE summary dataset from the cve-offline GitHub project.

This produces a smaller, pre-enriched dataset (~30-50k rows) with CVE ID,
description, CVSS score, and severity — without needing the slow NVD API
enrichment step.

Usage:
    python -m datasets.fetch_cve_summary
    python -m datasets.fetch_cve_summary --limit 10000

Output:
    datasets/output/cve_summary.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# This repo provides a clean, pre-parsed CSV of CVE data
CVE_SUMMARY_URL = (
    "https://raw.githubusercontent.com/cornerpirate/cve-offline/master/cve-summary.csv"
)
OUTPUT_DIR = Path(__file__).parent / "output"


def download_cve_summary(limit: int = 50_000) -> list[dict[str, str]]:
    """Download the CVE summary CSV from GitHub."""
    logger.info("Downloading CVE summary from %s …", CVE_SUMMARY_URL)

    req = urllib.request.Request(CVE_SUMMARY_URL, headers={"User-Agent": "HyperSearch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")

    # Parse CSV without header
    reader = csv.DictReader(
        io.StringIO(data),
        fieldnames=["cve_id", "severity", "description"]
    )
    rows = list(reader)

    logger.info("Parsed %d CVE records", len(rows))

    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]  # Keep the most recent CVEs
        logger.info("Subset to last %d rows", limit)

    return rows


def normalize_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Normalize column names and filter empty descriptions."""
    normalized = []
    for row in rows:
        # The CSV is headerless and we forced keys: cve_id, severity, description
        cve_id = row.get("cve_id", "")
        description = row.get("description", "")
        severity = row.get("severity", "")

        if not description or len(description.strip()) < 10:
            continue

        normalized.append({
            "cve": cve_id.strip(),
            "description": description.strip(),
            "cvss_score": "0.0",  # Not available in this dataset format
            "severity": severity.strip(),
        })

    logger.info("Normalized to %d rows (filtered empty descriptions)", len(normalized))
    return normalized


def save_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    """Write normalized rows to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["cve", "description", "cvss_score", "severity"]

    with open(output_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Saved %d rows to %s (%.1f MB)", len(rows), output_path, size_mb)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CVE summary dataset for HyperSearch")
    parser.add_argument(
        "--limit", type=int, default=50_000,
        help="Max rows to keep (0 = all). Default: 50000",
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_DIR / "cve_summary.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args()

    rows = download_cve_summary(limit=args.limit)
    rows = normalize_rows(rows)
    save_csv(rows, Path(args.output))

    logger.info("Done. Ready to ingest into HyperSearch:")
    logger.info("  hypersearch ingest cve_summary %s -t '{cve} {description}'", args.output)


if __name__ == "__main__":
    main()
