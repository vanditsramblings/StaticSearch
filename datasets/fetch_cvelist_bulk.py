#!/usr/bin/env python3
"""Fetch the complete CVE dataset (~250k+ records) from the cvelistV5 GitHub repository.

Downloads the latest ZIP release from github.com/CVEProject/cvelistV5, extracts it,
and parses all CVE JSON files. This provides the full dataset instantly compared
to the rate-limited NVD API.

Usage:
    python -m datasets.fetch_cvelist_bulk

Output:
    datasets/output/nvd_full.csv
"""

import argparse
import csv
import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"

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

def parse_cve_v5(vuln: dict) -> dict | None:
    """Extract a flat record from a single CVE v5 JSON object."""
    metadata = vuln.get("cveMetadata", {})
    state = metadata.get("state", "")
    if state == "REJECTED":
        return None
        
    cve_id = metadata.get("cveId", "")
    
    containers = vuln.get("containers", {})
    cna = containers.get("cna", {})
    
    # --- Description ---
    descriptions = cna.get("descriptions", [])
    description = ""
    for d in descriptions:
        if d.get("lang", "") == "en":
            description = d.get("value", "")
            break
    if not description and descriptions:
        description = descriptions[0].get("value", "")
        
    # --- CVSS metrics ---
    metrics = cna.get("metrics", [])
    cvss_score = ""
    severity = ""
    cvss_version = ""
    attack_vector = ""
    attack_complexity = ""
    privileges_required = ""
    user_interaction = ""
    
    for m in metrics:
        if "cvssV3_1" in m:
            cvss = m["cvssV3_1"]
            cvss_score = str(cvss.get("baseScore", ""))
            severity = cvss.get("baseSeverity", "")
            cvss_version = "3.1"
            attack_vector = cvss.get("attackVector", "")
            attack_complexity = cvss.get("attackComplexity", "")
            privileges_required = cvss.get("privilegesRequired", "")
            user_interaction = cvss.get("userInteraction", "")
            break
        elif "cvssV3_0" in m:
            cvss = m["cvssV3_0"]
            cvss_score = str(cvss.get("baseScore", ""))
            severity = cvss.get("baseSeverity", "")
            cvss_version = "3.0"
            attack_vector = cvss.get("attackVector", "")
            attack_complexity = cvss.get("attackComplexity", "")
            privileges_required = cvss.get("privilegesRequired", "")
            user_interaction = cvss.get("userInteraction", "")
            break
        elif "cvssV2_0" in m:
            cvss = m["cvssV2_0"]
            cvss_score = str(cvss.get("baseScore", ""))
            severity = cvss.get("baseSeverity", "")
            cvss_version = "2.0"
            attack_vector = cvss.get("accessVector", "")
            attack_complexity = cvss.get("accessComplexity", "")
            privileges_required = cvss.get("authentication", "")
            break
            
    # --- CWE ---
    problem_types = cna.get("problemTypes", [])
    cwe_id = ""
    if problem_types:
        for p in problem_types:
            for desc in p.get("descriptions", []):
                if "cweId" in desc:
                    cwe_id = desc["cweId"]
                    break
            if cwe_id:
                break
                
    # --- Affected products ---
    affected = cna.get("affected", [])
    vendors = set()
    products = set()
    versions = set()
    
    for a in affected:
        v = a.get("vendor", "")
        if v and v != "n/a": vendors.add(v)
        p = a.get("product", "")
        if p and p != "n/a": products.add(p)
        
        for ver in a.get("versions", []):
            version = ver.get("version", "")
            if version and version != "n/a":
                versions.add(version)
            lte = ver.get("lessThanOrEqual", "")
            if lte and lte != "*":
                versions.add(f"<={lte}")
            lt = ver.get("lessThan", "")
            if lt and lt != "*":
                versions.add(f"<{lt}")
                
    # --- Dates ---
    published = metadata.get("datePublished", "")
    modified = metadata.get("dateUpdated", "")
    
    # --- References ---
    references = cna.get("references", [])
    ref_urls = [r.get("url", "") for r in references[:5] if r.get("url")]

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
        "cwe_name": "",
        "published": published[:10] if published else "",
        "modified": modified[:10] if modified else "",
        "vendor": "|".join(sorted(vendors)[:5]),
        "product": "|".join(sorted(products)[:5]),
        "affected_versions": "|".join(sorted(versions)[:10]),
        "technologies": "|".join(sorted(products)[:10]), # Use products as tech proxy
        "reference_urls": "|".join(ref_urls),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT_DIR / "nvd_full.csv"))
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    
    logger.info("Fetching latest release zip URL from CVEProject/cvelistV5...")
    api_url = "https://api.github.com/repos/CVEProject/cvelistV5/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "HyperSearch"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            zip_url = data.get("zipball_url")
    except Exception as e:
        logger.error(f"Failed to get release info: {e}")
        return
        
    logger.info(f"Downloading {zip_url}...")
    req = urllib.request.Request(zip_url, headers={"User-Agent": "HyperSearch"})
    with urllib.request.urlopen(req) as resp:
        zip_bytes = resp.read()
        
    logger.info(f"Downloaded {len(zip_bytes)/(1024*1024):.1f} MB. Processing zip...")
    
    rows = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Filter only JSON files under cves/
        cve_files = [f for f in zf.namelist() if "/cves/" in f and f.endswith(".json") and "delta" not in f]
        logger.info(f"Found {len(cve_files)} JSON files in zip.")
        
        for i, fname in enumerate(cve_files):
            if i % 25000 == 0 and i > 0:
                logger.info(f"Processed {i} files...")
            with zf.open(fname) as f:
                try:
                    vuln = json.load(f)
                    parsed = parse_cve_v5(vuln)
                    if parsed and parsed.get("description"):
                        rows.append(parsed)
                except Exception as e:
                    pass

    logger.info(f"Parsed {len(rows)} valid CVEs. Saving to CSV...")
    
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        
    logger.info(f"Done. Saved to {out_path} ({out_path.stat().st_size/(1024*1024):.1f} MB).")

if __name__ == "__main__":
    main()
