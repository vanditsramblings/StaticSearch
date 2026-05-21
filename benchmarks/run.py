#!/usr/bin/env python3
"""Benchmark runner entry point — delegates to benchmarks package."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks import main

if __name__ == "__main__":
    main()
