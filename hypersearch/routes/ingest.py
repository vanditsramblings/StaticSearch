"""Ingestion endpoint — file upload or server-side path."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from hypersearch.config import Settings, get_settings
from hypersearch.hot_reload import run_staged_ingest
from hypersearch.models import IngestConfig, IngestResponse

router = APIRouter(prefix="/v1/collections", tags=["ingestion"])


@router.post(
    "/{name}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a dataset into a collection",
)
async def ingest_data(
    name: str,
    file: UploadFile | None = File(None),
    config_json: str | None = Form(None, alias="config"),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    """Upload CSV/JSON/Parquet data for embedding and indexing.

    Accepts either:
    - A multipart file upload (``file`` field), or
    - A server-side file path in ``config.file_path`` (for CLI / local use).

    Ingestion writes to a staging database and atomically swaps on completion.
    """
    # Parse optional config overrides
    cfg = IngestConfig()
    if config_json:
        import json
        cfg = IngestConfig(**json.loads(config_json))

    # Resolve data source
    if file and file.filename:
        content = await file.read()
        source: Path | io.BytesIO = io.BytesIO(content)
        filename = file.filename
    elif cfg.file_path:
        source = Path(cfg.file_path)
        filename = source.name
        if not source.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Server-side file not found: {cfg.file_path}",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either a file upload or a file_path in config",
        )

    # Ensure collection exists
    db_path = settings.storage.data_dir / f"{name}.duckdb"
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{name}' not found. Create it first via POST /v1/collections",
        )

    try:
        task_id, rows = run_staged_ingest(
            settings,
            name,
            source,
            filename,
            search_template=cfg.search_template,
            metadata_columns=cfg.metadata_columns,
            batch_size=cfg.batch_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        )

    return IngestResponse(task_id=task_id, status="completed", rows_queued=rows)
