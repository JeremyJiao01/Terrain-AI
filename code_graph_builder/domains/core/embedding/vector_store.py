"""Vector store for code embeddings.

This module provides abstract base class and implementations for storing
and searching code embeddings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from code_graph_builder.foundation.types.types import PropertyDict


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity (-1 to 1)

    Raises:
        ValueError: If vectors have different lengths or are zero vectors
    """
    import math

    if len(a) != len(b):
        raise ValueError(f"Vectors have different lengths: {len(a)} vs {len(b)}")

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        raise ValueError("Cannot compute cosine similarity for zero vectors")

    return dot_product / (norm_a * norm_b)


@dataclass
class VectorRecord:
    """A record in the vector store.

    Attributes:
        node_id: Unique node identifier
        qualified_name: Fully qualified name of the code entity
        embedding: Embedding vector
        metadata: Additional metadata
    """

    node_id: int
    qualified_name: str
    embedding: list[float]
    metadata: dict[str, str | int | float | None] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from vector similarity search.

    Attributes:
        node_id: Node identifier
        score: Similarity score (0-1, higher is better)
        qualified_name: Fully qualified name
    """

    node_id: int
    score: float
    qualified_name: str


class VectorStore(ABC):
    """Abstract base class for vector stores."""

    @abstractmethod
    def store_embedding(
        self,
        node_id: int,
        qualified_name: str,
        embedding: list[float],
        metadata: PropertyDict | None = None,
        **kwargs,
    ) -> None:
        """Store an embedding vector.

        Args:
            node_id: Unique node identifier
            qualified_name: Fully qualified name of the code entity
            embedding: Embedding vector
            metadata: Additional metadata
            **kwargs: Additional keyword arguments (implementation-specific)
        """
        ...

    @abstractmethod
    def store_embeddings_batch(
        self,
        records: list[VectorRecord],
    ) -> None:
        """Store multiple embeddings in batch.

        Args:
            records: List of vector records to store
        """
        ...

    @abstractmethod
    def search_similar(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filter_metadata: PropertyDict | None = None,
    ) -> list[SearchResult]:
        """Search for similar embeddings.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return
            filter_metadata: Optional metadata filter

        Returns:
            List of search results
        """
        ...

    @abstractmethod
    def delete_by_node_id(self, node_id: int) -> bool:
        """Delete an embedding by node ID.

        Args:
            node_id: Node identifier to delete

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Clear all embeddings from the store."""
        ...

    @abstractmethod
    def get_stats(self) -> dict[str, int]:
        """Get store statistics.

        Returns:
            Dictionary with statistics (count, dimension, etc.)
        """
        ...


class MemoryVectorStore(VectorStore):
    """In-memory vector store implementation.

    Uses cosine similarity for search. Suitable for testing and
    small datasets.

    Args:
        dimension: Expected embedding dimension
    """

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self._records: dict[int, VectorRecord] = {}

    def __len__(self) -> int:
        """Return the number of stored embeddings."""
        return len(self._records)

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Calculate cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity (-1 to 1)
        """
        try:
            return cosine_similarity(a, b)
        except ValueError:
            return 0.0

    def store_embedding(
        self,
        node_id: int,
        qualified_name: str,
        embedding: list[float],
        metadata: PropertyDict | None = None,
        **kwargs,
    ) -> None:
        """Store an embedding vector in memory.

        Args:
            node_id: Unique node identifier
            qualified_name: Fully qualified name of the code entity
            embedding: Embedding vector
            metadata: Additional metadata dictionary
            **kwargs: Additional keyword arguments (stored as metadata)

        Raises:
            ValueError: If embedding dimension doesn't match or embedding is empty
        """
        if not embedding:
            raise ValueError("Embedding cannot be empty")

        if len(embedding) != self.dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, got {len(embedding)}"
            )

        meta: dict[str, str | int | float | None] = {}
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, type(None))):
                    meta[k] = v
                elif isinstance(v, list):
                    meta[k] = str(v)
                elif isinstance(v, bool):
                    meta[k] = int(v)

        # Store additional kwargs as metadata
        for k, v in kwargs.items():
            if isinstance(v, (str, int, float, type(None))):
                meta[k] = v
            elif isinstance(v, list):
                meta[k] = str(v)
            elif isinstance(v, bool):
                meta[k] = int(v)

        self._records[node_id] = VectorRecord(
            node_id=node_id,
            qualified_name=qualified_name,
            embedding=embedding,
            metadata=meta,
        )

    def store_embeddings_batch(
        self,
        records: list[VectorRecord],
    ) -> None:
        """Store multiple embeddings in batch."""
        for record in records:
            self._records[record.node_id] = record

    def search_similar(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filter_metadata: PropertyDict | None = None,
    ) -> list[SearchResult]:
        """Search for similar embeddings using cosine similarity."""
        if top_k < 0:
            raise ValueError(f"top_k must be non-negative, got {top_k}")

        if not self._records:
            return []

        scores: list[tuple[int, float, str]] = []

        for node_id, record in self._records.items():
            if filter_metadata:
                match = all(
                    record.metadata.get(k) == v for k, v in filter_metadata.items()
                )
                if not match:
                    continue

            similarity = self._cosine_similarity(query_embedding, record.embedding)
            scores.append((node_id, similarity, record.qualified_name))

        scores.sort(key=lambda x: x[1], reverse=True)

        return [
            SearchResult(
                node_id=node_id,
                score=round(score, 4),
                qualified_name=qn,
            )
            for node_id, score, qn in scores[:top_k]
        ]

    def delete_by_node_id(self, node_id: int) -> bool:
        """Delete an embedding by node ID."""
        if node_id in self._records:
            del self._records[node_id]
            return True
        return False

    # Alias for compatibility with tests
    delete_embedding = delete_by_node_id

    def get_embedding(self, node_id: int) -> "VectorRecord | None":
        """Get an embedding record by node ID.

        Args:
            node_id: Node identifier

        Returns:
            VectorRecord if found, None otherwise
        """
        return self._records.get(node_id)

    def clear(self) -> None:
        """Clear all embeddings."""
        self._records.clear()

    def get_stats(self) -> dict[str, int]:
        """Get store statistics."""
        return {
            "count": len(self._records),
            "dimension": self.dimension,
        }

    def get_all_records(self) -> list[VectorRecord]:
        """Get all records (for testing/debugging).

        Returns:
            List of all vector records
        """
        return list(self._records.values())


class QdrantVectorStore(VectorStore):
    """Qdrant-based vector store implementation.

    Requires qdrant-client to be installed.

    Args:
        collection_name: Name of the Qdrant collection
        dimension: Embedding dimension
        db_path: Path for local Qdrant storage (optional)
        host: Qdrant server host (if not using local)
        port: Qdrant server port
    """

    def __init__(
        self,
        collection_name: str = "code_embeddings",
        dimension: int = 1024,
        db_path: str | Path | None = None,
        host: str | None = None,
        port: int = 6333,
    ):
        self.collection_name = collection_name
        self.dimension = dimension
        self.db_path = Path(db_path) if db_path else None
        self.host = host
        self.port = port

        self._client: "QdrantClient | None" = None
        self._initialized = False

    def _lazy_init(self) -> None:
        """Lazy initialization of Qdrant client."""
        if self._initialized:
            return

        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            if self.db_path:
                self._client = QdrantClient(path=str(self.db_path))
            elif self.host:
                self._client = QdrantClient(host=self.host, port=self.port)
            else:
                self._client = QdrantClient(location=":memory:")

            if not self._client.collection_exists(self.collection_name):
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.dimension,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection: {self.collection_name}")

            self._initialized = True

        except ImportError as e:
            logger.error(f"Failed to import qdrant-client: {e}")
            raise RuntimeError(
                "qdrant-client required for QdrantVectorStore. "
                "Install with: pip install qdrant-client"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant: {e}")
            raise

    def store_embedding(
        self,
        node_id: int,
        qualified_name: str,
        embedding: list[float],
        metadata: PropertyDict | None = None,
        **kwargs,
    ) -> None:
        """Store an embedding vector in Qdrant."""
        self._lazy_init()

        from qdrant_client.models import PointStruct

        payload: dict[str, str | int | float | None] = {
            "node_id": node_id,
            "qualified_name": qualified_name,
        }
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, type(None))):
                    payload[k] = v
                elif isinstance(v, list):
                    payload[k] = str(v)
                elif isinstance(v, bool):
                    payload[k] = int(v)

        # Store additional kwargs as metadata
        for k, v in kwargs.items():
            if isinstance(v, (str, int, float, type(None))):
                payload[k] = v
            elif isinstance(v, list):
                payload[k] = str(v)
            elif isinstance(v, bool):
                payload[k] = int(v)

        assert self._client is not None
        self._client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=node_id,
                    vector=embedding,
                    payload=payload,
                )
            ],
        )

    def store_embeddings_batch(
        self,
        records: list[VectorRecord],
    ) -> None:
        """Store multiple embeddings in batch."""
        self._lazy_init()

        from qdrant_client.models import PointStruct

        points = []
        for record in records:
            payload: dict[str, str | int | float | None] = {
                "node_id": record.node_id,
                "qualified_name": record.qualified_name,
            }
            payload.update(record.metadata)

            points.append(
                PointStruct(
                    id=record.node_id,
                    vector=record.embedding,
                    payload=payload,
                )
            )

        if points:
            assert self._client is not None
            self._client.upsert(
                collection_name=self.collection_name,
                points=points,
            )

    def search_similar(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filter_metadata: PropertyDict | None = None,
    ) -> list[SearchResult]:
        """Search for similar embeddings in Qdrant."""
        self._lazy_init()

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        search_filter = None
        if filter_metadata:
            conditions = []
            for k, v in filter_metadata.items():
                if isinstance(v, (str, int)):
                    conditions.append(
                        FieldCondition(key=k, match=MatchValue(value=v))
                    )
            if conditions:
                search_filter = Filter(must=conditions)

        assert self._client is not None
        results = self._client.query_points(
            collection_name=self.collection_name,
            query=query_embedding,
            limit=top_k,
            query_filter=search_filter,
        )

        return [
            SearchResult(
                node_id=hit.payload["node_id"],
                score=hit.score,
                qualified_name=str(hit.payload.get("qualified_name", "")),
            )
            for hit in results.points
            if hit.payload is not None
        ]

    def delete_by_node_id(self, node_id: int) -> bool:
        """Delete an embedding by node ID."""
        self._lazy_init()

        assert self._client is not None
        result = self._client.delete(
            collection_name=self.collection_name,
            points_selector=[node_id],
        )

        return result.operation_id is not None

    def clear(self) -> None:
        """Clear all embeddings."""
        self._lazy_init()

        assert self._client is not None
        self._client.delete_collection(self.collection_name)
        self._initialized = False
        self._lazy_init()

    def get_stats(self) -> dict[str, int]:
        """Get store statistics."""
        self._lazy_init()

        assert self._client is not None
        info = self._client.get_collection(self.collection_name)

        return {
            "count": info.points_count,
            "dimension": self.dimension,
        }


def create_vector_store(
    backend: str = "memory",
    dimension: int = 1024,
    **kwargs: str | int | Path | None,
) -> VectorStore:
    """Factory function to create vector store.

    Args:
        backend: Backend type ("memory" or "qdrant")
        dimension: Embedding dimension
        **kwargs: Additional arguments for specific backends

    Returns:
        VectorStore instance

    Raises:
        ValueError: If backend is unknown
    """
    if backend == "memory":
        return MemoryVectorStore(dimension=dimension)
    elif backend == "qdrant":
        return QdrantVectorStore(
            dimension=dimension,
            collection_name=str(kwargs.get("collection_name", "code_embeddings")),
            db_path=kwargs.get("db_path"),
            host=kwargs.get("host"),
            port=int(kwargs.get("port", 6333)),
        )
    else:
        raise ValueError(f"Unknown vector store backend: {backend}")
