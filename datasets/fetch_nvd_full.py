#!/usr/bin/env python3
"""Fetch the complete NVD CVE dataset (~300k records) with rich metadata.

Downloads all CVEs from the NVD API 2.0 in paginated batches, extracting:
- CVE ID, description, CVSS scores (v3.1/v4.0), severity
- CWE IDs, affected products/vendors (from CPE), published/modified dates
- Attack vector, attack complexity, references
- Technologies and affected packages (extracted from CPE strings)

Supports resume via checkpoint files for interrupted downloads.

Usage:
    python -m datasets.fetch_nvd_full                                    # Full download
    python -m datasets.fetch_nvd_full --limit 10000                      # First 10k
    python -m datasets.fetch_nvd_full --nvd-api-key YOUR_KEY             # Faster with API key
    python -m datasets.fetch_nvd_full --resume                           # Resume interrupted

Output:
    datasets/output/nvd_full.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
OUTPUT_DIR = Path(__file__).parent / "output"
CHECKPOINT_DIR = OUTPUT_DIR / ".checkpoints"

# NVD API 2.0 limits
RESULTS_PER_PAGE = 2000
# Rate limits: 5 requests per 30s without key, 50 per 30s with key
DELAY_NO_KEY = 6.5        # seconds between requests (safe margin)
DELAY_WITH_KEY = 0.65     # seconds between requests with API key

# CSV output fields
FIELDNAMES = [
    "cve",
    "description",
    "cvss_score",
    "severity",
    "cvss_version",
    "attack_vector",
    "attack_complexity",
    "privileges_required",
    "user_interaction",
    "cwe_id",
    "cwe_name",
    "published",
    "modified",
    "vendor",
    "product",
    "affected_versions",
    "technologies",
    "reference_urls",
]


def fetch_page(
    start_index: int,
    api_key: str | None = None,
    results_per_page: int = RESULTS_PER_PAGE,
) -> dict:
    """Fetch a single page of CVE results from NVD API 2.0."""
    url = f"{NVD_API_URL}?startIndex={start_index}&resultsPerPage={results_per_page}"

    headers = {"User-Agent": "HyperSearch/0.1"}
    if api_key:
        headers["apiKey"] = api_key

    req = urllib.request.Request(url, headers=headers)

    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # Rate limited — back off
                wait = (attempt + 1) * 30
                logger.warning("Rate limited (403). Waiting %ds before retry %d/%d", wait, attempt + 1, max_retries)
                time.sleep(wait)
            elif e.code == 503:
                wait = (attempt + 1) * 15
                logger.warning("Service unavailable (503). Waiting %ds before retry %d/%d", wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            wait = (attempt + 1) * 10
            logger.warning("Connection error: %s. Waiting %ds before retry %d/%d", e, wait, attempt + 1, max_retries)
            time.sleep(wait)

    raise ConnectionError(f"Failed to fetch page at startIndex={start_index} after {max_retries} retries")


def parse_cve(vuln: dict) -> dict:
    """Extract a flat record from a single NVD vulnerability object."""
    cve_data = vuln.get("cve", {})
    cve_id = cve_data.get("id", "")

    # --- Description (English) ---
    descriptions = cve_data.get("descriptions", [])
    description = next(
        (d["value"] for d in descriptions if d.get("lang") == "en"),
        "",
    )

    # --- CVSS metrics (prefer v3.1 > v4.0 > v3.0 > v2.0) ---
    metrics = cve_data.get("metrics", {})
    cvss_score = ""
    severity = ""
    cvss_version = ""
    attack_vector = ""
    attack_complexity = ""
    privileges_required = ""
    user_interaction = ""

    for version_key, ver_label in [
        ("cvssMetricV31", "3.1"),
        ("cvssMetricV40", "4.0"),
        ("cvssMetricV30", "3.0"),
        ("cvssMetricV2", "2.0"),
    ]:
        metric_list = metrics.get(version_key, [])
        if metric_list:
            cvss_data = metric_list[0].get("cvssData", {})
            cvss_score = str(cvss_data.get("baseScore", ""))
            severity = cvss_data.get("baseSeverity", "")
            cvss_version = ver_label
            attack_vector = cvss_data.get("attackVector", "")
            attack_complexity = cvss_data.get("attackComplexity", "")
            privileges_required = cvss_data.get("privilegesRequired", "")
            user_interaction = cvss_data.get("userInteraction", "")
            break

    # --- CWE ---
    weaknesses = cve_data.get("weaknesses", [])
    cwe_id = ""
    cwe_name = ""
    if weaknesses:
        for w in weaknesses:
            cwe_desc = w.get("description", [])
            if cwe_desc:
                val = cwe_desc[0].get("value", "")
                if val and val != "NVD-CWE-noinfo" and val != "NVD-CWE-Other":
                    cwe_id = val
                    break
        if not cwe_id and weaknesses[0].get("description"):
            cwe_id = weaknesses[0]["description"][0].get("value", "")

    # --- Affected products/technologies (from CPE) ---
    configurations = cve_data.get("configurations", [])
    vendors = set()
    products = set()
    versions = set()
    technologies = set()

    for config in configurations:
        for node in config.get("nodes", []):
            for cpe_match in node.get("cpeMatch", []):
                criteria = cpe_match.get("criteria", "")
                parts = criteria.split(":")
                if len(parts) >= 5:
                    vendor = parts[3] if parts[3] != "*" else ""
                    product = parts[4] if parts[4] != "*" else ""
                    version = parts[5] if len(parts) > 5 and parts[5] != "*" else ""

                    if vendor:
                        vendors.add(vendor)
                    if product:
                        products.add(product)
                        technologies.add(product)
                    if version and version not in ("-", "*"):
                        versions.add(version)

                # Version range info
                ver_start = cpe_match.get("versionStartIncluding", "")
                ver_end = cpe_match.get("versionEndExcluding", "") or cpe_match.get("versionEndIncluding", "")
                if ver_start:
                    versions.add(f">={ver_start}")
                if ver_end:
                    versions.add(f"<{ver_end}")

    # --- Dates ---
    published = cve_data.get("published", "")
    modified = cve_data.get("lastModified", "")

    # --- References ---
    references = cve_data.get("references", [])
    ref_urls = [r.get("url", "") for r in references[:5]]  # Top 5 refs

    return {
        "cve": cve_id,
        "description": description,
        "cvss_score": cvss_score,
        "severity": severity,
        "cvss_version": cvss_version,
        "attack_vector": attack_vector,
        "attack_complexity": attack_complexity,
        "privileges_required": privileges_required,
        "user_interaction": user_interaction,
        "cwe_id": cwe_id,
        "cwe_name": cwe_name,
        "published": published[:10] if published else "",  # Date only
        "modified": modified[:10] if modified else "",
        "vendor": "|".join(sorted(vendors)[:5]),
        "product": "|".join(sorted(products)[:5]),
        "affected_versions": "|".join(sorted(versions)[:10]),
        "technologies": "|".join(sorted(technologies)[:10]),
        "reference_urls": "|".join(ref_urls),
    }


def save_checkpoint(start_index: int, rows: list[dict], checkpoint_dir: Path) -> None:
    """Save a batch checkpoint for resume capability."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_dir / f"batch_{start_index:08d}.json"
    with open(path, "w") as f:
        json.dump({"start_index": start_index, "count": len(rows), "rows": rows}, f)


def load_checkpoints(checkpoint_dir: Path) -> tuple[list[dict], int]:
    """Load all saved checkpoints and return (rows, last_start_index)."""
    if not checkpoint_dir.exists():
        return [], 0

    all_rows = []
    max_index = 0

    for path in sorted(checkpoint_dir.glob("batch_*.json")):
        with open(path) as f:
            data = json.load(f)
        all_rows.extend(data["rows"])
        max_index = max(max_index, data["start_index"] + data["count"])

    if all_rows:
        logger.info("Resumed from checkpoint: %d rows, next index: %d", len(all_rows), max_index)

    return all_rows, max_index


def download_nvd_full(
    limit: int = 0,
    api_key: str | None = None,
    resume: bool = True,
    checkpoint_dir: Path = CHECKPOINT_DIR,
) -> list[dict]:
    """Download the complete NVD CVE dataset.

    Args:
        limit: Max CVEs to download (0 = all, ~280k+)
        api_key: NVD API key for higher rate limits
        resume: Whether to resume from checkpoints
        checkpoint_dir: Directory for checkpoint files

    Returns:
        List of parsed CVE records
    """
    delay = DELAY_WITH_KEY if api_key else DELAY_NO_KEY
    logger.info(
        "Starting NVD download (delay=%.1fs per request, API key: %s)",
        delay,
        "yes" if api_key else "no",
    )

    # Resume from checkpoints if available
    all_rows: list[dict] = []
    start_index = 0
    if resume:
        all_rows, start_index = load_checkpoints(checkpoint_dir)

    # First request to get totalResults
    logger.info("Fetching total CVE count from NVD...")
    initial = fetch_page(0, api_key=api_key, results_per_page=1)
    total_results = initial.get("totalResults", 0)
    logger.info("NVD reports %d total CVEs", total_results)

    if limit > 0:
        total_results = min(total_results, limit)

    # Paginated download
    page_count = 0
    while start_index < total_results:
        batch_size = min(RESULTS_PER_PAGE, total_results - start_index)

        logger.info(
            "Fetching CVEs %d–%d / %d (%.1f%%) …",
            start_index,
            start_index + batch_size,
            total_results,
            (start_index / total_results) * 100,
        )

        try:
            data = fetch_page(start_index, api_key=api_key, results_per_page=batch_size)
        except ConnectionError as e:
            logger.error("Failed at index %d: %s. Saving progress.", start_index, e)
            break

        vulns = data.get("vulnerabilities", [])
        if not vulns:
            logger.warning("Empty page at index %d, advancing", start_index)
            start_index += batch_size
            continue

        batch_rows = [parse_cve(v) for v in vulns]

        # Filter out empty descriptions
        batch_rows = [r for r in batch_rows if r.get("description") and len(r["description"]) > 10]

        all_rows.extend(batch_rows)

        # Save checkpoint
        save_checkpoint(start_index, batch_rows, checkpoint_dir)

        start_index += len(vulns)
        page_count += 1

        # Rate limiting
        time.sleep(delay)

        # Progress log every 10 pages
        if page_count % 10 == 0:
            logger.info(
                "Progress: %d CVEs fetched (%d pages, %.1f%%)",
                len(all_rows),
                page_count,
                (start_index / total_results) * 100,
            )

    logger.info("Download complete: %d CVE records fetched in %d pages", len(all_rows), page_count)
    return all_rows


def save_csv(rows: list[dict], output_path: Path) -> None:
    """Write parsed CVE rows to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Saved %d rows to %s (%.1f MB)", len(rows), output_path, size_mb)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch complete NVD CVE dataset for HyperSearch")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max CVEs to download (0 = all ~300k). Default: 0",
    )
    parser.add_argument(
        "--nvd-api-key", type=str, default=os.getenv("NVD_API_KEY"),
        help="NVD API key for faster downloads (env: NVD_API_KEY)",
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_DIR / "nvd_full.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Resume from checkpoints (default: true)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Start fresh, ignoring checkpoints",
    )
    args = parser.parse_args()

    resume = not args.no_resume

    if args.no_resume:
        # Clear old checkpoints
        import shutil
        if CHECKPOINT_DIR.exists():
            shutil.rmtree(CHECKPOINT_DIR)
            logger.info("Cleared old checkpoints")

    rows = download_nvd_full(
        limit=args.limit,
        api_key=args.nvd_api_key,
        resume=resume,
    )

    save_csv(rows, Path(args.output))

    # Print summary stats
    severities = {}
    for r in rows:
        sev = r.get("severity", "UNKNOWN") or "UNKNOWN"
        severities[sev] = severities.get(sev, 0) + 1

    print(f"\n{'='*60}")
    print(f"NVD Dataset Summary")
    print(f"{'='*60}")
    print(f"Total CVEs:  {len(rows):,}")
    print(f"Output:      {args.output}")
    print(f"\nSeverity Distribution:")
    for sev, count in sorted(severities.items()):
        print(f"  {sev:12s}: {count:,}")
    print(f"{'='*60}")

    logger.info("Done. Ready to ingest into HyperSearch:")
    logger.info("  hypersearch ingest nvd_cves %s -t '{cve} {description}'", args.output)


if __name__ == "__main__":
    main()
