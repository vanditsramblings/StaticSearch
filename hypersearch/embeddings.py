"""EmbeddingManager — lazy-loads sentence-transformers and batch-encodes text."""

from __future__ import annotations

import logging
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


def embed(
    texts: list[str],
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
    batch_size: int = 256,
) -> np.ndarray:
    """Encode *texts* into a ``(N, D)`` float32 array.

    Processes in batches of *batch_size* to cap peak memory.
    """
    model = _get_model(model_name, device)

    all_embeddings: list[np.ndarray] = []
    total = len(texts)

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        vectors = model.encode(batch, show_progress_bar=False, convert_to_numpy=True)
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
