"""Tests for the embedding module — model loading, caching, and encoding."""

from __future__ import annotations

import numpy as np
import pytest


class TestEmbedBatch:
    """Test batch embedding functionality."""

    def test_embed_returns_correct_shape(self):
        from hypersearch.embeddings import embed

        vectors = embed(["hello world", "foo bar"])
        assert vectors.shape == (2, 384)
        assert vectors.dtype == np.float32

    def test_embed_single_text(self):
        from hypersearch.embeddings import embed

        vectors = embed(["single text"])
        assert vectors.shape == (1, 384)

    def test_embed_normalized(self):
        from hypersearch.embeddings import embed

        vectors = embed(["test normalization"])
        norms = np.linalg.norm(vectors, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    def test_embed_batching(self):
        """Verify batching produces same results as all-at-once."""
        from hypersearch.embeddings import embed

        texts = [f"document number {i}" for i in range(10)]
        v1 = embed(texts, batch_size=3)
        v2 = embed(texts, batch_size=100)
        np.testing.assert_allclose(v1, v2, atol=1e-5)


class TestEmbedQuery:
    """Test single-query embedding with LRU cache."""

    def test_embed_query_returns_1d(self):
        from hypersearch.embeddings import embed_query

        vec = embed_query("test query")
        assert vec.ndim == 1
        assert vec.shape == (384,)
        assert vec.dtype == np.float32

    def test_embed_query_normalized(self):
        from hypersearch.embeddings import embed_query

        vec = embed_query("test normalization")
        np.testing.assert_allclose(np.linalg.norm(vec), 1.0, atol=1e-5)

    def test_embed_query_caching(self):
        from hypersearch.embeddings import clear_cache, embed_query

        clear_cache()
        v1 = embed_query("cached query")
        v2 = embed_query("cached query")
        np.testing.assert_array_equal(v1, v2)

    def test_embed_query_different_texts(self):
        from hypersearch.embeddings import embed_query

        v1 = embed_query("remote code execution")
        v2 = embed_query("SQL injection attack")
        # Different texts should produce different vectors
        assert not np.allclose(v1, v2)


class TestCacheManagement:
    """Test cache configuration and clearing."""

    def test_clear_cache(self):
        from hypersearch.embeddings import _query_cache, clear_cache, embed_query

        embed_query("fill cache")
        assert len(_query_cache) > 0
        clear_cache()
        assert len(_query_cache) == 0

    def test_configure_cache_size(self):
        from hypersearch.embeddings import _query_cache_max, configure_cache

        original = _query_cache_max
        configure_cache(512)
        from hypersearch import embeddings

        assert embeddings._query_cache_max == 512
        configure_cache(original)  # restore


class TestModelManagement:
    """Test model loading and info."""

    def test_dimensions(self):
        from hypersearch.embeddings import dimensions

        assert dimensions() == 384

    def test_warmup(self):
        from hypersearch.embeddings import warmup

        # Should not raise
        warmup()

    def test_model_reuse(self):
        """Loading same model twice should reuse the singleton."""
        from hypersearch.embeddings import _get_model

        m1 = _get_model("all-MiniLM-L6-v2")
        m2 = _get_model("all-MiniLM-L6-v2")
        assert m1 is m2
