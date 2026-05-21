"""Configuration loader — YAML file + environment overrides via Pydantic Settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Sub-models for nested YAML sections
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    api_key: str | None = None


class StorageConfig(BaseModel):
    data_dir: Path = Path("./data")
    snapshot_dir: Path = Path("./snapshots")


class EmbeddingConfig(BaseModel):
    model: str = "all-MiniLM-L6-v2"
    batch_size: int = 256
    device: str = "cpu"
    dimensions: int = 384
    # HNSW index tuning — higher values = better recall but slower build/search
    hnsw_m: int = 48                    # Max edges per node (default 16, higher for large datasets)
    hnsw_ef_construction: int = 256     # Build-time beam width (default 200)
    hnsw_ef_search: int = 128           # Query-time beam width (default 64, higher = better recall)
    # Query embedding cache for typeahead performance
    query_cache_size: int = 2048        # LRU cache entries for query embeddings


class DefaultsConfig(BaseModel):
    search_template: str = "{text}"
    metadata_columns: list[str] = Field(default_factory=list)
    top_k: int = 10


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Merged configuration from YAML file + environment variables.

    Load order: YAML defaults → env vars (prefixed ``HYPER_``).
    """

    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)

    model_config = {"env_prefix": "HYPER_", "env_nested_delimiter": "__"}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML config file and return its contents as a dict."""
    if not path.exists():
        return {}
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton factory — resolves YAML path from ``HYPER_CONFIG`` env var
    or falls back to ``./hypersearch.yaml``.
    """
    config_path = Path(os.getenv("HYPER_CONFIG", "hypersearch.yaml"))
    overrides = _load_yaml(config_path)
    settings = Settings(**overrides)

    # Ensure storage directories exist
    settings.storage.data_dir.mkdir(parents=True, exist_ok=True)
    settings.storage.snapshot_dir.mkdir(parents=True, exist_ok=True)

    return settings
