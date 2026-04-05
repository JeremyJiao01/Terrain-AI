"""Embeddings module for code semantic search.

This module provides embedding functionality for code using Qwen3 models.
"""

from __future__ import annotations

from .qwen3_embedder import (
    BaseEmbedder,
    DummyEmbedder,
    Qwen3Embedder,
    create_embedder,
    last_token_pool,
)
from .vector_store import (
    MemoryVectorStore,
    QdrantVectorStore,
    SearchResult,
    VectorRecord,
    VectorStore,
    cosine_similarity,
    create_vector_store,
)

__all__ = [
    # Embedders
    "BaseEmbedder",
    "DummyEmbedder",
    "Qwen3Embedder",
    "create_embedder",
    "last_token_pool",
    # Vector stores
    "VectorStore",
    "MemoryVectorStore",
    "QdrantVectorStore",
    "VectorRecord",
    "SearchResult",
    "create_vector_store",
    "cosine_similarity",
]
