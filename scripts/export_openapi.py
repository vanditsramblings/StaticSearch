#!/usr/bin/env python3
"""Export the OpenAPI specification from the FastAPI app to a JSON file.

Usage:
    python scripts/export_openapi.py
    python scripts/export_openapi.py --output docs/openapi.yaml --format yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export HyperSearch OpenAPI spec")
    parser.add_argument(
        "--output", "-o", default="openapi.json",
        help="Output file path (default: openapi.json)",
    )
    parser.add_argument(
        "--format", "-f", choices=["json", "yaml"], default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args()

    from hypersearch.server import app

    spec = app.openapi()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "yaml":
        try:
            import yaml
            with open(output_path, "w") as fh:
                yaml.dump(spec, fh, default_flow_style=False, sort_keys=False)
        except ImportError:
            print("PyYAML required for YAML output. Install with: pip install pyyaml")
            sys.exit(1)
    else:
        with open(output_path, "w") as fh:
            json.dump(spec, fh, indent=2)

    print(f"OpenAPI spec exported to {output_path}")


if __name__ == "__main__":
    main()
