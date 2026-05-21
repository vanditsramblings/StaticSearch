"""EmbeddingManager — lazy-loads sentence-transformers and batch-encodes text.

Includes an LRU cache for single-query embeddings (typeahead optimization)
and normalized embeddings for faster distance computation.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_model: SentenceTransformer | None = None
_model_name: str = ""


def _get_model(model_name: str, device: str = "cpu") -> SentenceTransformer:
    """Load the model on first call, then reuse globally."""
    global _model, _model_name  # noqa: PLW0603

    if _model is not None and _model_name == model_name:
        return _model

    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model '%s' on device '%s' …", model_name, device)
    _model = SentenceTransformer(model_name, device=device)
    _model_name = model_name
    logger.info("Model loaded — embedding dimension: %d", _model.get_sentence_embedding_dimension())
    return _model


# ---------------------------------------------------------------------------
# Query embedding cache (LRU) for typeahead performance
# ---------------------------------------------------------------------------

_query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
_query_cache_max: int = 2048


def configure_cache(max_size: int) -> None:
    """Set the maximum query embedding cache size."""
    global _query_cache_max  # noqa: PLW0603
    _query_cache_max = max_size


def _cache_key(text: str, model_name: str) -> str:
    """Fast cache key for query text + model combo."""
    return hashlib.md5(f"{model_name}:{text}".encode(), usedforsecurity=False).hexdigest()


def embed_query(
    text: str,
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
) -> np.ndarray:
    """Embed a single query string with LRU caching.

    Returns a normalized 1-D float32 vector. Cached results are returned
    in <0.01ms, making this ideal for typeahead search.
    """
    key = _cache_key(text, model_name)

    if key in _query_cache:
        # Move to end (most recently used)
        _query_cache.move_to_end(key)
        return _query_cache[key]

    model = _get_model(model_name, device)
    vector = model.encode(
        text,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)

    # Add to cache, evict oldest if full
    _query_cache[key] = vector
    while len(_query_cache) > _query_cache_max:
        _query_cache.popitem(last=False)

    return vector


def embed(
    texts: list[str],
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
    batch_size: int = 256,
) -> np.ndarray:
    """Encode *texts* into a ``(N, D)`` float32 array.

    Processes in batches of *batch_size* to cap peak memory.
    Embeddings are L2-normalized for cosine similarity via dot product.
    """
    model = _get_model(model_name, device)

    all_embeddings: list[np.ndarray] = []
    total = len(texts)

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        vectors = model.encode(
            batch,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        all_embeddings.append(vectors)

        if total > batch_size:
            logger.debug(
                "Embedded batch %d–%d / %d",
                start,
                min(start + batch_size, total),
                total,
            )

    return np.vstack(all_embeddings).astype(np.float32)


def dimensions(model_name: str = "all-MiniLM-L6-v2", device: str = "cpu") -> int:
    """Return the embedding dimensionality for *model_name*."""
    return int(_get_model(model_name, device).get_sentence_embedding_dimension())


def warmup(model_name: str = "all-MiniLM-L6-v2", device: str = "cpu") -> None:
    """Pre-load the model and run a dummy encoding to warm JIT/caches.

    Call this at server startup to avoid cold-start latency on first query.
    """
    model = _get_model(model_name, device)
    # Dummy encode to warm internal buffers
    model.encode(["warmup"], show_progress_bar=False, convert_to_numpy=True)
    logger.info("Embedding model warmed up and ready")


def clear_cache() -> None:
    """Clear the query embedding cache. Used by tests."""
    _query_cache.clear()
