#!/usr/bin/env bash
# ============================================================================
# HyperSearch Bootstrap Script
# ============================================================================
#
# One-command setup: installs dependencies, downloads a demo dataset,
# ingests it, and starts the server.
#
# Usage:
#   ./bootstrap.sh              # Full setup (install + dataset + ingest + serve)
#   ./bootstrap.sh --install    # Dependencies only
#   ./bootstrap.sh --dataset    # Download dataset only
#   ./bootstrap.sh --ingest     # Ingest dataset only (requires prior install + dataset)
#   ./bootstrap.sh --serve      # Start server only
#
# Requirements:
#   - Python 3.11+
#   - pip (or uv)
#
# ============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
DATASET_DIR="${PROJECT_DIR}/datasets/output"
DATASET_FILE="${DATASET_DIR}/cve_summary.csv"
COLLECTION_NAME="cve_vulnerabilities"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

log()    { echo -e "${CYAN}[HyperSearch]${NC} $*"; }
success(){ echo -e "${GREEN}[✓]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[✗]${NC} $*" >&2; }

# ============================================================================
# Step 1: Install dependencies
# ============================================================================
install_deps() {
    log "Setting up Python environment …"

    # Check Python version
    PYTHON=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            version=$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON="$candidate"
                break
            fi
        fi
    done

    if [ -z "$PYTHON" ]; then
        error "Python 3.11+ is required. Found: $(python3 --version 2>/dev/null || echo 'none')"
        exit 1
    fi

    success "Using $PYTHON ($($PYTHON --version))"

    # Create venv if needed
    if [ ! -d "$VENV_DIR" ]; then
        log "Creating virtual environment …"
        $PYTHON -m venv "$VENV_DIR"
        success "Virtual environment created at ${VENV_DIR}"
    fi

    # Activate venv
    source "${VENV_DIR}/bin/activate"

    # Install with uv if available, otherwise pip
    if command -v uv &>/dev/null; then
        log "Installing dependencies with uv …"
        uv pip install -e ".[dev]"
    else
        log "Installing dependencies with pip …"
        pip install --upgrade pip
        pip install -e ".[dev]"
    fi

    success "Dependencies installed"
}

# ============================================================================
# Step 2: Download dataset
# ============================================================================
download_dataset() {
    log "Downloading CVE/EPSS demo dataset …"

    source "${VENV_DIR}/bin/activate" 2>/dev/null || true

    if [ -f "$DATASET_FILE" ]; then
        row_count=$(wc -l < "$DATASET_FILE" | tr -d ' ')
        warn "Dataset already exists (${row_count} rows): ${DATASET_FILE}"
        read -p "Re-download? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi

    mkdir -p "$DATASET_DIR"

    # Try the fast CVE summary dataset first
    log "Fetching CVE summary dataset (pre-enriched with descriptions) …"
    $PYTHON -m datasets.fetch_cve_summary --limit 50000 --output "$DATASET_FILE"

    if [ -f "$DATASET_FILE" ]; then
        row_count=$(wc -l < "$DATASET_FILE" | tr -d ' ')
        size=$(du -h "$DATASET_FILE" | cut -f1)
        success "Dataset ready: ${row_count} rows, ${size} → ${DATASET_FILE}"
    else
        error "Dataset download failed"
        exit 1
    fi
}

# ============================================================================
# Step 3: Ingest into HyperSearch
# ============================================================================
ingest_dataset() {
    log "Ingesting dataset into HyperSearch …"

    source "${VENV_DIR}/bin/activate"

    if [ ! -f "$DATASET_FILE" ]; then
        error "Dataset not found: ${DATASET_FILE}"
        error "Run: ./bootstrap.sh --dataset"
        exit 1
    fi

    # Copy config
    if [ ! -f "${PROJECT_DIR}/hypersearch.yaml" ]; then
        cp "${PROJECT_DIR}/hypersearch.example.yaml" "${PROJECT_DIR}/hypersearch.yaml"
        success "Created hypersearch.yaml from example"
    fi

    # Run ingest via CLI
    log "Ingesting ${DATASET_FILE} into collection '${COLLECTION_NAME}' …"
    $PYTHON -m hypersearch.cli ingest \
        "$COLLECTION_NAME" \
        "$DATASET_FILE" \
        --template "{cve} {description}" \
        --metadata "cve,cvss_score,severity"

    success "Ingestion complete"
}

# ============================================================================
# Step 4: Start server
# ============================================================================
start_server() {
    log "Starting HyperSearch API server …"

    source "${VENV_DIR}/bin/activate"

    if [ ! -f "${PROJECT_DIR}/hypersearch.yaml" ]; then
        cp "${PROJECT_DIR}/hypersearch.example.yaml" "${PROJECT_DIR}/hypersearch.yaml"
    fi

    echo ""
    echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║              HyperSearch API Server                       ║${NC}"
    echo -e "${BOLD}${CYAN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${BOLD}${CYAN}║${NC}  API:    http://127.0.0.1:8000                             ${BOLD}${CYAN}║${NC}"
    echo -e "${BOLD}${CYAN}║${NC}  Docs:   http://127.0.0.1:8000/docs                        ${BOLD}${CYAN}║${NC}"
    echo -e "${BOLD}${CYAN}║${NC}  Health: http://127.0.0.1:8000/health                      ${BOLD}${CYAN}║${NC}"
    echo -e "${BOLD}${CYAN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${BOLD}${CYAN}║${NC}  Press Ctrl+C to stop                                     ${BOLD}${CYAN}║${NC}"
    echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    $PYTHON -m uvicorn hypersearch.server:app --host 0.0.0.0 --port 8000 --log-level info
}

# ============================================================================
# Step 5: Export OpenAPI spec
# ============================================================================
export_openapi() {
    log "Exporting OpenAPI specification …"

    source "${VENV_DIR}/bin/activate"

    $PYTHON -c "
import json
from hypersearch.server import app
spec = app.openapi()
with open('openapi.json', 'w') as f:
    json.dump(spec, f, indent=2)
print(f'OpenAPI spec exported to openapi.json ({len(json.dumps(spec))} bytes)')
"

    success "OpenAPI spec saved to openapi.json"
}

# ============================================================================
# Main
# ============================================================================
print_usage() {
    echo -e "${BOLD}HyperSearch Bootstrap${NC}"
    echo ""
    echo "Usage: ./bootstrap.sh [OPTION]"
    echo ""
    echo "Options:"
    echo "  (no args)      Full setup: install → dataset → ingest → serve"
    echo "  --install      Install Python dependencies only"
    echo "  --dataset      Download demo dataset only"
    echo "  --ingest       Ingest dataset into HyperSearch"
    echo "  --serve        Start the API server"
    echo "  --openapi      Export the OpenAPI specification"
    echo "  --bench        Run the benchmark harness"
    echo "  --help         Show this help"
}

case "${1:-full}" in
    --install)
        install_deps
        ;;
    --dataset)
        download_dataset
        ;;
    --ingest)
        ingest_dataset
        ;;
    --serve)
        start_server
        ;;
    --openapi)
        export_openapi
        ;;
    --bench)
        source "${VENV_DIR}/bin/activate"
        $PYTHON -m benchmarks.run "${@:2}"
        ;;
    --help|-h)
        print_usage
        ;;
    full)
        install_deps
        download_dataset
        ingest_dataset
        echo ""
        success "Setup complete! Starting server …"
        echo ""
        start_server
        ;;
    *)
        error "Unknown option: $1"
        print_usage
        exit 1
        ;;
esac
