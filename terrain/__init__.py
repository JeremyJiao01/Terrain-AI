"""Terrain - 代码知识图谱构建库.

This library provides functionality to build knowledge graphs from source code,
supporting multiple programming languages and multiple storage backends.

Backends:
    - Kùzu (default): Embedded graph database, no Docker required
    - Memgraph: Full-featured graph database (requires Docker)
    - Memory: In-memory storage, no persistence

Example:
    >>> from terrain import TerrainBuilder
    >>>
    >>> # Using Kùzu (recommended, no Docker)
    >>> builder = TerrainBuilder("/path/to/repo", backend="kuzu")
    >>> result = builder.build_graph()
    >>>
    >>> # Using Memory (for testing)
    >>> builder = TerrainBuilder("/path/to/repo", backend="memory")
    >>> data = builder.export_graph()
"""

from .domains.core.graph.builder import TerrainBuilder
from .foundation.types.config import (
    ConfigValidator,
    EmbeddingConfig,
    KuzuConfig,
    MemgraphConfig,
    MemoryConfig,
    OutputConfig,
    ScanConfig,
)
from .domains.core.embedding import (
    BaseEmbedder,
    DummyEmbedder,
    MemoryVectorStore,
    QdrantVectorStore,
    Qwen3Embedder,
    SearchResult,
    VectorRecord,
    VectorStore,
    cosine_similarity,
    create_embedder,
    create_vector_store,
    last_token_pool,
)
from .foundation.services.kuzu_service import KuzuIngestor
from .foundation.services.memory_service import MemoryIngestor
from .foundation.types.types import BuildResult, GraphData, GraphSummary

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("terrain-ai")
except Exception:
    __version__ = "0.1.0"
__all__ = [
    # Main API
    "TerrainBuilder",
    "BuildResult",
    "GraphData",
    "GraphSummary",
    # Backend implementations
    "KuzuIngestor",
    "MemoryIngestor",
    # Configuration classes
    "ConfigValidator",
    "EmbeddingConfig",
    "KuzuConfig",
    "MemgraphConfig",
    "MemoryConfig",
    "OutputConfig",
    "ScanConfig",
    # Embeddings
    "BaseEmbedder",
    "DummyEmbedder",
    "Qwen3Embedder",
    "create_embedder",
    "last_token_pool",
    "VectorStore",
    "MemoryVectorStore",
    "QdrantVectorStore",
    "VectorRecord",
    "SearchResult",
    "create_vector_store",
    "cosine_similarity",
]
