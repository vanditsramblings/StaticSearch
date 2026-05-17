"""Typer CLI — serve, ingest, search, snapshot commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="hypersearch",
    help="HyperSearch — BYOD semantic search engine",
    no_args_is_help=True,
)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8000, help="Bind port"),
    workers: int = typer.Option(1, help="Uvicorn worker count"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to hypersearch.yaml"),
) -> None:
    """Start the HyperSearch API server."""
    import os

    import uvicorn

    if config:
        os.environ["HYPER_CONFIG"] = config

    uvicorn.run(
        "hypersearch.server:app",
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )


@app.command()
def ingest(
    collection: str = typer.Argument(..., help="Collection name"),
    file: Path = typer.Argument(..., help="Path to CSV/JSON/Parquet file", exists=True),
    template: Optional[str] = typer.Option(None, "--template", "-t", help="Search template string"),
    metadata: Optional[str] = typer.Option(None, "--metadata", "-m", help="Comma-separated metadata columns"),
    batch_size: Optional[int] = typer.Option(None, "--batch-size", "-b", help="Embedding batch size"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to hypersearch.yaml"),
) -> None:
    """Ingest a dataset into a collection (with atomic hot-reload)."""
    import os

    if config:
        os.environ["HYPER_CONFIG"] = config

    from hypersearch.config import get_settings
    from hypersearch.db import connect
    from hypersearch.hot_reload import run_staged_ingest

    settings = get_settings()
    meta_cols = [c.strip() for c in metadata.split(",")] if metadata else None

    # Ensure the collection DB exists
    connect(settings, collection)

    task_id, rows = run_staged_ingest(
        settings,
        collection,
        file,
        file.name,
        search_template=template,
        metadata_columns=meta_cols,
        batch_size=batch_size,
    )

    typer.echo(f"✓ Ingested {rows:,} rows into '{collection}' (task: {task_id})")


@app.command()
def search(
    collection: str = typer.Argument(..., help="Collection name"),
    query: str = typer.Argument(..., help="Search query text"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Number of results"),
    filters_json: Optional[str] = typer.Option(None, "--filters", "-f", help="JSON filter object"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to hypersearch.yaml"),
) -> None:
    """Run a semantic search query against a collection."""
    import os

    if config:
        os.environ["HYPER_CONFIG"] = config

    from hypersearch.config import get_settings
    from hypersearch.db import connect
    from hypersearch.engine import search as run_search

    settings = get_settings()
    conn = connect(settings, collection)
    filters = json.loads(filters_json) if filters_json else None

    results, latency = run_search(conn, query, settings=settings, top_k=top_k, filters=filters)

    typer.echo(f"Found {len(results)} results in {latency:.1f}ms\n")
    for i, r in enumerate(results, 1):
        typer.echo(f"  [{i}] score={r['score']:.4f}")
        typer.echo(f"      doc: {r['document'][:120]}")
        if r["metadata"]:
            typer.echo(f"      meta: {json.dumps(r['metadata'], default=str)}")
        typer.echo()


@app.command()
def snapshot(
    collection: str = typer.Argument(..., help="Collection name"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to hypersearch.yaml"),
) -> None:
    """Create a backup snapshot of a collection."""
    import os

    if config:
        os.environ["HYPER_CONFIG"] = config

    from hypersearch.config import get_settings
    from hypersearch.snapshot import create_snapshot

    settings = get_settings()
    result = create_snapshot(settings, collection)

    typer.echo(f"✓ Snapshot saved: {result['snapshot_path']} ({result['size_bytes']:,} bytes)")


@app.command()
def restore(
    collection: str = typer.Argument(..., help="Collection name"),
    snapshot_path: str = typer.Argument(..., help="Path to snapshot .duckdb file"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to hypersearch.yaml"),
) -> None:
    """Restore a collection from a snapshot file."""
    import os

    if config:
        os.environ["HYPER_CONFIG"] = config

    from hypersearch.config import get_settings
    from hypersearch.snapshot import restore_snapshot

    settings = get_settings()
    restore_snapshot(settings, collection, snapshot_path)

    typer.echo(f"✓ Collection '{collection}' restored from {snapshot_path}")


if __name__ == "__main__":
    app()
