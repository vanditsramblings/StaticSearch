#!/usr/bin/env python3
"""Fetch the FIRST EPSS dataset and enrich with CVE descriptions from NVD.

Downloads the full EPSS CSV (~240k CVEs), then enriches a configurable subset
with vulnerability descriptions from the NVD API to create a rich semantic
search dataset.

Usage:
    python -m datasets.fetch_epss                          # default 50k rows
    python -m datasets.fetch_epss --limit 10000            # smaller subset
    python -m datasets.fetch_epss --limit 0 --skip-enrich  # full EPSS only (no NVD)

Output:
    datasets/output/epss_enriched.csv
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EPSS_URL = "https://epss.cyentia.com/epss_scores-current.csv.gz"
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
OUTPUT_DIR = Path(__file__).parent / "output"

# NVD API allows 50 CVEs per request in batch mode
NVD_BATCH_SIZE = 50
NVD_RATE_LIMIT_DELAY = 0.7  # seconds between requests (respect rate limits)


def download_epss(limit: int = 50_000) -> list[dict[str, str]]:
    """Download and parse the EPSS CSV from FIRST/Cyentia.

    Returns a list of dicts with keys: cve, epss, percentile.
    """
    logger.info("Downloading EPSS scores from %s …", EPSS_URL)

    req = urllib.request.Request(EPSS_URL, headers={"User-Agent": "HyperSearch/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()

    # Decompress gzip
    data = gzip.decompress(raw).decode("utf-8")
    lines = data.strip().splitlines()

    # The first line is a comment (model version/date), skip it
    # The second line is the CSV header: cve,epss,percentile
    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("cve,"):
            start_idx = i
            break

    reader = csv.DictReader(lines[start_idx:])
    rows = list(reader)

    logger.info("Parsed %d EPSS records", len(rows))

    if limit > 0:
        # Sort by EPSS score descending — keep the most exploitable CVEs
        rows.sort(key=lambda r: float(r.get("epss", "0")), reverse=True)
        rows = rows[:limit]
        logger.info("Subset to top %d by EPSS score", limit)

    return rows


def enrich_with_nvd(
    rows: list[dict[str, str]],
    api_key: str | None = None,
) -> list[dict[str, str]]:
    """Fetch CVE descriptions and severity from NVD API 2.0.

    Enriches each row with: description, severity, cvss_score, cwe_id, published.
    """
    total = len(rows)
    logger.info("Enriching %d CVEs with NVD data (this may take a while) …", total)

    cve_ids = [r["cve"] for r in rows]
    enriched: dict[str, dict[str, str]] = {}

    headers = {"User-Agent": "HyperSearch/0.1"}
    if api_key:
        headers["apiKey"] = api_key

    for batch_start in range(0, len(cve_ids), NVD_BATCH_SIZE):
        batch = cve_ids[batch_start : batch_start + NVD_BATCH_SIZE]

        for cve_id in batch:
            try:
                url = f"{NVD_API_URL}?cveId={cve_id}"
                req = urllib.request.Request(url, headers=headers)

                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode())

                vulns = body.get("vulnerabilities", [])
                if not vulns:
                    continue

                cve_data = vulns[0].get("cve", {})

                # Extract English description
                descriptions = cve_data.get("descriptions", [])
                desc = next(
                    (d["value"] for d in descriptions if d.get("lang") == "en"),
                    "",
                )

                # Extract CVSS v3.1 or v3.0 score
                metrics = cve_data.get("metrics", {})
                cvss_score = ""
                severity = ""

                for version_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40"):
                    metric_list = metrics.get(version_key, [])
                    if metric_list:
                        cvss_data = metric_list[0].get("cvssData", {})
                        cvss_score = str(cvss_data.get("baseScore", ""))
                        severity = cvss_data.get("baseSeverity", "")
                        break

                # CWE
                weaknesses = cve_data.get("weaknesses", [])
                cwe_id = ""
                if weaknesses:
                    cwe_desc = weaknesses[0].get("description", [])
                    if cwe_desc:
                        cwe_id = cwe_desc[0].get("value", "")

                published = cve_data.get("published", "")

                enriched[cve_id] = {
                    "description": desc,
                    "severity": severity,
                    "cvss_score": cvss_score,
                    "cwe_id": cwe_id,
                    "published": published,
                }

            except Exception as exc:
                logger.debug("Failed to enrich %s: %s", cve_id, exc)

            time.sleep(NVD_RATE_LIMIT_DELAY)

        done = min(batch_start + NVD_BATCH_SIZE, total)
        logger.info("NVD enrichment progress: %d / %d", done, total)

    # Merge enrichment into rows
    for row in rows:
        cve_id = row["cve"]
        extra = enriched.get(cve_id, {})
        row["description"] = extra.get("description", "")
        row["severity"] = extra.get("severity", "")
        row["cvss_score"] = extra.get("cvss_score", "")
        row["cwe_id"] = extra.get("cwe_id", "")
        row["published"] = extra.get("published", "")

    enriched_count = sum(1 for r in rows if r.get("description"))
    logger.info("Enriched %d / %d rows with NVD descriptions", enriched_count, total)

    return rows


def save_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    """Write rows to a CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["cve", "epss", "percentile", "description", "severity", "cvss_score", "cwe_id", "published"]
    # Filter to only include columns that exist in the data
    available = set(rows[0].keys()) if rows else set()
    fieldnames = [f for f in fieldnames if f in available]

    with open(output_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Saved %d rows to %s (%.1f MB)", len(rows), output_path, size_mb)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch EPSS + NVD dataset for HyperSearch")
    parser.add_argument(
        "--limit", type=int, default=50_000,
        help="Max rows to keep (0 = all, sorted by EPSS desc). Default: 50000",
    )
    parser.add_argument(
        "--skip-enrich", action="store_true",
        help="Skip NVD enrichment (produces EPSS-only CSV — fast but no descriptions)",
    )
    parser.add_argument(
        "--nvd-api-key", type=str, default=None,
        help="NVD API key for higher rate limits (optional, get one at https://nvd.nist.gov/developers/request-an-api-key)",
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_DIR / "epss_enriched.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args()

    rows = download_epss(limit=args.limit)

    if not args.skip_enrich:
        rows = enrich_with_nvd(rows, api_key=args.nvd_api_key)

    save_csv(rows, Path(args.output))
    logger.info("Done. Ready to ingest into HyperSearch:")
    logger.info("  hypersearch ingest cve_epss %s -t '{cve} {description}'", args.output)


if __name__ == "__main__":
    main()
