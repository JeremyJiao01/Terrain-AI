"""Tests for MemoryVectorStore - In-memory vector storage for code embeddings.

These tests verify the MemoryVectorStore class correctly:
1. Stores embeddings with associated metadata
2. Searches for similar vectors using cosine similarity
3. Handles edge cases (empty store, single item, etc.)
4. Provides efficient similarity computation
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence


# Module-level fixtures for all test classes
@pytest.fixture
def vector_store() -> "MemoryVectorStore":
    """Create a fresh MemoryVectorStore instance."""
    from code_graph_builder.embeddings.vector_store import MemoryVectorStore

    return MemoryVectorStore(dimension=768)


@pytest.fixture
def sample_embedding() -> list[float]:
    """Create a sample embedding vector."""
    return [0.1] * 768


@pytest.fixture
def sample_embeddings() -> list[list[float]]:
    """Create multiple sample embeddings."""
    return [
        [1.0] + [0.0] * 767,  # First dimension high
        [0.0, 1.0] + [0.0] * 766,  # Second dimension high
        [0.5, 0.5] + [0.0] * 766,  # Mixed
    ]


class TestMemoryVectorStore:
    """Test suite for MemoryVectorStore class."""

    def test_store_initialization(self) -> None:
        """Test MemoryVectorStore initializes correctly."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        assert store is not None
        assert len(store) == 0

    def test_store_embedding_adds_item(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test store_embedding adds an item to the store."""
        vector_store.store_embedding(
            node_id=1,
            embedding=sample_embedding,
            qualified_name="test.module.function",
        )

        assert len(vector_store) == 1

    def test_store_embedding_stores_correct_data(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test store_embedding stores correct data."""
        vector_store.store_embedding(
            node_id=42,
            embedding=sample_embedding,
            qualified_name="myproject.utils.foo",
            start_line=10,
            end_line=20,
            path="/path/to/file.py",
        )

        results = vector_store.search_similar(sample_embedding, top_k=1)

        assert len(results) == 1
        assert results[0].node_id == 42
        assert results[0].qualified_name == "myproject.utils.foo"

    def test_store_multiple_embeddings(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test storing multiple embeddings."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        assert len(vector_store) == 3

    def test_search_similar_returns_top_k(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test search_similar returns top_k results."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        # Search with query similar to first embedding
        query = [0.9] + [0.1] * 767
        results = vector_store.search_similar(query, top_k=2)

        assert len(results) == 2

    def test_search_similar_orders_by_similarity(self, vector_store: "MemoryVectorStore") -> None:
        """Test search_similar orders results by similarity."""
        # Store embeddings with different directions
        embedding1 = [1.0, 0.0, 0.0] + [0.0] * 765  # Direction A
        embedding2 = [0.0, 1.0, 0.0] + [0.0] * 765  # Direction B (orthogonal)
        embedding3 = [0.99, 0.01, 0.0] + [0.0] * 765  # Similar to A

        vector_store.store_embedding(node_id=1, embedding=embedding1, qualified_name="func1")
        vector_store.store_embedding(node_id=2, embedding=embedding2, qualified_name="func2")
        vector_store.store_embedding(node_id=3, embedding=embedding3, qualified_name="func3")

        # Query similar to embedding1
        query = [1.0, 0.0, 0.0] + [0.0] * 765
        results = vector_store.search_similar(query, top_k=3)

        # Most similar should be first
        assert results[0].node_id in [1, 3]  # Both similar to query
        assert results[0].score > results[1].score

    def test_search_similar_empty_store(self, vector_store: "MemoryVectorStore") -> None:
        """Test search_similar on empty store returns empty list."""
        query = [0.1] * 768
        results = vector_store.search_similar(query, top_k=5)

        assert results == []

    def test_search_similar_single_item(self, vector_store: "MemoryVectorStore") -> None:
        """Test search_similar with single item in store."""
        embedding = [0.1] * 768
        vector_store.store_embedding(
            node_id=1,
            embedding=embedding,
            qualified_name="test.func",
        )

        query = [0.1] * 768
        results = vector_store.search_similar(query, top_k=5)

        assert len(results) == 1
        assert results[0].node_id == 1

    def test_search_similar_top_k_larger_than_store(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test search_similar when top_k > store size."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        query = [0.1] * 768
        results = vector_store.search_similar(query, top_k=10)

        assert len(results) == 3  # Only 3 items in store

    def test_search_similar_zero_top_k(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test search_similar with top_k=0 returns empty list."""
        vector_store.store_embedding(
            node_id=1,
            embedding=sample_embedding,
            qualified_name="test.func",
        )

        query = [0.1] * 768
        results = vector_store.search_similar(query, top_k=0)

        assert results == []

    def test_search_similar_negative_top_k(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test search_similar with negative top_k raises error."""
        vector_store.store_embedding(
            node_id=1,
            embedding=sample_embedding,
            qualified_name="test.func",
        )

        query = [0.1] * 768

        with pytest.raises(ValueError):
            vector_store.search_similar(query, top_k=-1)

    def test_update_existing_embedding(self, vector_store: "MemoryVectorStore") -> None:
        """Test updating an existing embedding by node_id."""
        embedding1 = [1.0] + [0.0] * 767
        embedding2 = [0.0, 1.0] + [0.0] * 766

        vector_store.store_embedding(
            node_id=1,
            embedding=embedding1,
            qualified_name="test.func",
        )

        # Update with new embedding
        vector_store.store_embedding(
            node_id=1,
            embedding=embedding2,
            qualified_name="test.func_updated",
        )

        assert len(vector_store) == 1

        # Search should find the updated embedding
        query = [0.0, 1.0] + [0.0] * 766
        results = vector_store.search_similar(query, top_k=1)

        assert results[0].qualified_name == "test.func_updated"

    def test_delete_embedding(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test delete_embedding removes item."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        vector_store.delete_embedding(node_id=2)

        assert len(vector_store) == 2

        # Verify it's gone
        results = vector_store.search_similar(sample_embeddings[1], top_k=3)
        node_ids = [r.node_id for r in results]
        assert 2 not in node_ids

    def test_delete_nonexistent_embedding(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test delete_embedding for non-existent node_id is no-op."""
        vector_store.store_embedding(
            node_id=1,
            embedding=sample_embedding,
            qualified_name="test.func",
        )

        vector_store.delete_embedding(node_id=999)  # Non-existent

        assert len(vector_store) == 1

    def test_clear_store(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test clear removes all embeddings."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        vector_store.clear()

        assert len(vector_store) == 0

    def test_get_stats(self, vector_store: "MemoryVectorStore", sample_embeddings: list[list[float]]) -> None:
        """Test get_stats returns correct statistics."""
        for i, embedding in enumerate(sample_embeddings):
            vector_store.store_embedding(
                node_id=i + 1,
                embedding=embedding,
                qualified_name=f"test.func{i + 1}",
            )

        stats = vector_store.get_stats()

        assert stats["count"] == 3
        assert stats["dimension"] == 768

    def test_get_embedding_by_node_id(self, vector_store: "MemoryVectorStore", sample_embedding: list[float]) -> None:
        """Test get_embedding retrieves embedding by node_id."""
        vector_store.store_embedding(
            node_id=42,
            embedding=sample_embedding,
            qualified_name="test.func",
        )

        result = vector_store.get_embedding(node_id=42)

        assert result is not None
        assert result.node_id == 42
        assert result.embedding == sample_embedding

    def test_get_embedding_nonexistent(self, vector_store: "MemoryVectorStore") -> None:
        """Test get_embedding returns None for non-existent node_id."""
        result = vector_store.get_embedding(node_id=999)

        assert result is None

    def test_dimension_mismatch_raises_error(self, vector_store: "MemoryVectorStore") -> None:
        """Test storing embedding with wrong dimension raises error."""
        vector_store.store_embedding(
            node_id=1,
            embedding=[0.1] * 768,
            qualified_name="test.func1",
        )

        # Try to store with different dimension
        with pytest.raises(ValueError):
            vector_store.store_embedding(
                node_id=2,
                embedding=[0.1] * 512,  # Wrong dimension
                qualified_name="test.func2",
            )


class TestCosineSimilarity:
    """Test suite for cosine similarity computation."""

    def test_cosine_similarity_identical_vectors(self) -> None:
        """Test cosine similarity of identical vectors is 1.0."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 2.0, 3.0]
        v2 = [1.0, 2.0, 3.0]

        result = cosine_similarity(v1, v2)

        assert abs(result - 1.0) < 1e-6

    def test_cosine_similarity_opposite_vectors(self) -> None:
        """Test cosine similarity of opposite vectors is -1.0."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 2.0, 3.0]
        v2 = [-1.0, -2.0, -3.0]

        result = cosine_similarity(v1, v2)

        assert abs(result - (-1.0)) < 1e-6

    def test_cosine_similarity_orthogonal_vectors(self) -> None:
        """Test cosine similarity of orthogonal vectors is 0.0."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]

        result = cosine_similarity(v1, v2)

        assert abs(result) < 1e-6

    def test_cosine_similarity_different_magnitudes(self) -> None:
        """Test cosine similarity is independent of vector magnitude."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 0.0, 0.0]
        v2 = [5.0, 0.0, 0.0]

        result = cosine_similarity(v1, v2)

        assert abs(result - 1.0) < 1e-6

    def test_cosine_similarity_zero_vector_raises(self) -> None:
        """Test cosine similarity with zero vector raises error."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 2.0, 3.0]
        v2 = [0.0, 0.0, 0.0]

        with pytest.raises(ValueError):
            cosine_similarity(v1, v2)

    def test_cosine_similarity_different_lengths_raises(self) -> None:
        """Test cosine similarity with different length vectors raises error."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 2.0, 3.0]
        v2 = [1.0, 2.0]

        with pytest.raises(ValueError):
            cosine_similarity(v1, v2)

    def test_cosine_similarity_typical_case(self) -> None:
        """Test cosine similarity with typical vectors."""
        from code_graph_builder.embeddings.vector_store import cosine_similarity

        v1 = [1.0, 2.0, 3.0]
        v2 = [4.0, 5.0, 6.0]

        # Manual calculation
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(a * a for a in v2))
        expected = dot_product / (norm1 * norm2)

        result = cosine_similarity(v1, v2)

        assert abs(result - expected) < 1e-6


class TestVectorStoreEdgeCases:
    """Test suite for edge cases in MemoryVectorStore."""

    def test_empty_embedding_raises(self) -> None:
        """Test storing empty embedding raises error."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        with pytest.raises(ValueError):
            store.store_embedding(
                node_id=1,
                embedding=[],
                qualified_name="test.func",
            )

    def test_very_large_embedding(self) -> None:
        """Test storing very large embedding."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        large_embedding = [0.001] * 10000
        store = MemoryVectorStore(dimension=len(large_embedding))

        store.store_embedding(
            node_id=1,
            embedding=large_embedding,
            qualified_name="test.func",
        )

        assert len(store) == 1

    def test_special_characters_in_qualified_name(self) -> None:
        """Test storing with special characters in qualified_name."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        store.store_embedding(
            node_id=1,
            embedding=[0.1] * 768,
            qualified_name="test.module.function<T>",
        )

        result = store.get_embedding(node_id=1)
        assert result.qualified_name == "test.module.function<T>"

    def test_unicode_in_qualified_name(self) -> None:
        """Test storing with unicode characters in qualified_name."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        store.store_embedding(
            node_id=1,
            embedding=[0.1] * 768,
            qualified_name="测试.函数.示例",
        )

        result = store.get_embedding(node_id=1)
        assert result.qualified_name == "测试.函数.示例"

    def test_negative_node_id(self) -> None:
        """Test storing with negative node_id."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        store.store_embedding(
            node_id=-1,
            embedding=[0.1] * 768,
            qualified_name="test.func",
        )

        result = store.get_embedding(node_id=-1)
        assert result.node_id == -1

    def test_float_similarity_scores(self, vector_store: "MemoryVectorStore") -> None:
        """Test that similarity scores are valid floats between -1 and 1."""
        vector_store.store_embedding(
            node_id=1,
            embedding=[1.0, 0.0] + [0.0] * 766,
            qualified_name="test.func1",
        )

        vector_store.store_embedding(
            node_id=2,
            embedding=[0.0, 1.0] + [0.0] * 766,
            qualified_name="test.func2",
        )

        query = [0.5, 0.5] + [0.0] * 766
        results = vector_store.search_similar(query, top_k=2)

        for result in results:
            assert isinstance(result.score, float)
            assert -1.0 <= result.score <= 1.0


class TestVectorStoreIntegration:
    """Integration tests for MemoryVectorStore."""

    def test_store_and_retrieve_roundtrip(self) -> None:
        """Test full roundtrip of store and retrieve."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        # Store multiple embeddings
        embeddings = [
            ([1.0, 0.0] + [0.0] * 766, "func1"),
            ([0.0, 1.0] + [0.0] * 766, "func2"),
            ([0.5, 0.5] + [0.0] * 766, "func3"),
        ]

        for i, (emb, name) in enumerate(embeddings):
            store.store_embedding(
                node_id=i + 1,
                embedding=emb,
                qualified_name=f"test.{name}",
            )

        # Search with query similar to func1
        query = [0.9, 0.1] + [0.0] * 766
        results = store.search_similar(query, top_k=3)

        assert len(results) == 3
        # func1 should be most similar
        assert results[0].qualified_name == "test.func1"

    def test_multiple_searches_consistency(self) -> None:
        """Test that multiple searches return consistent results."""
        from code_graph_builder.embeddings.vector_store import MemoryVectorStore

        store = MemoryVectorStore(dimension=768)

        store.store_embedding(
            node_id=1,
            embedding=[1.0, 0.0] + [0.0] * 766,
            qualified_name="test.func1",
        )

        store.store_embedding(
            node_id=2,
            embedding=[0.0, 1.0] + [0.0] * 766,
            qualified_name="test.func2",
        )

        query = [0.5, 0.5] + [0.0] * 766

        results1 = store.search_similar(query, top_k=2)
        results2 = store.search_similar(query, top_k=2)

        assert len(results1) == len(results2)
        for r1, r2 in zip(results1, results2):
            assert r1.node_id == r2.node_id
            assert abs(r1.score - r2.score) < 1e-6
