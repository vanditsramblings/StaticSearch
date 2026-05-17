"""FastAPI application factory — lifespan, router assembly, middleware."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from hypersearch import __version__
from hypersearch.config import get_settings
from hypersearch.db import disconnect_all
from hypersearch.middleware import ApiKeyMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle hook."""
    settings = get_settings()
    logger.info(
        "HyperSearch v%s starting — data_dir=%s, model=%s",
        __version__,
        settings.storage.data_dir,
        settings.embedding.model,
    )
    yield
    disconnect_all()
    logger.info("HyperSearch shutdown complete")


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="HyperSearch",
        description="BYOD semantic search engine — embedded DuckDB + local embeddings",
        version=__version__,
        lifespan=lifespan,
    )

    # --- Middleware ---
    app.add_middleware(ApiKeyMiddleware, api_key=settings.server.api_key)

    # --- Routers ---
    from hypersearch.routes.collections import router as collections_router
    from hypersearch.routes.health import router as health_router
    from hypersearch.routes.ingest import router as ingest_router
    from hypersearch.routes.search import router as search_router
    from hypersearch.routes.snapshots import router as snapshots_router

    app.include_router(collections_router)
    app.include_router(ingest_router)
    app.include_router(search_router)
    app.include_router(snapshots_router)
    app.include_router(health_router)

    return app


# Default instance for ``uvicorn hypersearch.server:app``
app = create_app()
