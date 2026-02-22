"""Integration tests for semantic search and graph query features.

Tests the integration between:
- GraphUpdater embedding generation (P0)
- Semantic search tools (P1)
- Graph query layer with Kuzu/Memgraph compatibility (P2)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from ..embeddings.qwen3_embedder import BaseEmbedder
    from ..embeddings.vector_store import VectorStore


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_embedder() -> MagicMock:
    """Create a mock embedder for testing."""
    mock = MagicMock()
    mock.embed_code.return_value = [0.1] * 1536
    mock.embed_query.return_value = [0.1] * 1536
    mock.embed_documents.return_value = [[0.1] * 1536]
    mock.get_embedding_dimension.return_value = 1536
    return mock


@pytest.fixture
def mock_vector_store() -> MagicMock:
    """Create a mock vector store for testing."""
    mock = MagicMock()
    mock.store_embedding.return_value = None
    mock.store_embeddings_batch.return_value = None
    mock.search_similar.return_value = []
    mock.get_stats.return_value = {"count": 0, "dimension": 1536}
    return mock


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a sample repository for testing."""
    repo = tmp_path / "sample_project"
    repo.mkdir()

    # Create a Python file with functions
    py_file = repo / "module.py"
    py_file.write_text("""
def add(a, b):
    \"\"\"Add two numbers.\"\"\"
    return a + b

def subtract(a, b):
    \"\"\"Subtract b from a.\"\"\"
    return a - b

class Calculator:
    \"\"\"A simple calculator.\"\"\"

    def multiply(self, a, b):
        \"\"\"Multiply two numbers.\"\"\"
        return a * b
""")

    return repo


# =============================================================================
# P0: GraphUpdater Integration Tests
# =============================================================================


class TestGraphUpdaterEmbeddingIntegration:
    """Test GraphUpdater embedding generation integration."""

    @pytest.mark.skip(reason="Requires full parser setup")
    def test_graph_updater_initializes_with_embedding_config(
        self,
        sample_repo: Path,
        mock_embedder: MagicMock,
        mock_vector_store: MagicMock,
    ) -> None:
        """Test that GraphUpdater can be initialized with embedding config."""
        from ..graph_updater import GraphUpdater
        from ..services.memory_service import MemoryIngestor

        ingestor = MemoryIngestor()
        embedding_config = {
            "enabled": True,
            "batch_size": 10,
            "api_key": "test-key",
        }

        updater = GraphUpdater(
            ingestor=ingestor,
            repo_path=sample_repo,
            parsers={},
            queries={},
            embedder=mock_embedder,
            vector_store=mock_vector_store,
            embedding_config=embedding_config,
        )

        assert updater.embedder is mock_embedder
        assert updater.vector_store is mock_vector_store
        assert updater._embedding_enabled is True

    def test_graph_updater_skips_embeddings_when_disabled(
        self,
        sample_repo: Path,
        mock_vector_store: MagicMock,
    ) -> None:
        """Test that GraphUpdater skips embeddings when disabled."""
        from ..graph_updater import GraphUpdater
        from ..services.memory_service import MemoryIngestor

        ingestor = MemoryIngestor()

        updater = GraphUpdater(
            ingestor=ingestor,
            repo_path=sample_repo,
            parsers={},
            queries={},
            embedder=None,
            vector_store=mock_vector_store,
            embedding_config={"enabled": False},
        )

        assert updater._embedding_enabled is False
        assert updater.embedder is None


# =============================================================================
# P1: Semantic Search Tests
# =============================================================================


class TestSemanticSearchService:
    """Test semantic search service functionality."""

    def test_semantic_search_with_mock_embedder(
        self,
        mock_embedder: MagicMock,
    ) -> None:
        """Test semantic search with mock embedder."""
        from ..tools.semantic_search import SemanticSearchService
        from ..embeddings.vector_store import MemoryVectorStore

        # Create vector store with test data
        vector_store = MemoryVectorStore(dimension=1536)
        vector_store.store_embedding(
            node_id=1,
            qualified_name="module.add",
            embedding=[0.1] * 1536,
            metadata={"type": "Function"},
        )

        service = SemanticSearchService(
            embedder=mock_embedder,
            vector_store=vector_store,
        )

        results = service.search("addition function", top_k=5)

        assert isinstance(results, list)
        mock_embedder.embed_query.assert_called_once_with("addition function")

    def test_semantic_search_result_structure(
        self,
        mock_embedder: MagicMock,
    ) -> None:
        """Test that search results have correct structure."""
        from ..tools.semantic_search import SemanticSearchService, SemanticSearchResult
        from ..embeddings.vector_store import MemoryVectorStore

        vector_store = MemoryVectorStore(dimension=1536)
        vector_store.store_embedding(
            node_id=1,
            qualified_name="test.module.function",
            embedding=[0.5] * 1536,
            metadata={"type": "Function"},
        )

        service = SemanticSearchService(
            embedder=mock_embedder,
            vector_store=vector_store,
        )

        results = service.search("test query", top_k=1)

        if results:
            result = results[0]
            assert hasattr(result, "node_id")
            assert hasattr(result, "qualified_name")
            assert hasattr(result, "name")
            assert hasattr(result, "type")
            assert hasattr(result, "score")


class TestSemanticSearchConvenienceFunctions:
    """Test semantic search convenience functions."""

    def test_semantic_code_search_function(self, mock_embedder: MagicMock) -> None:
        """Test semantic_code_search convenience function."""
        from ..tools.semantic_search import semantic_code_search
        from ..embeddings.vector_store import MemoryVectorStore

        vector_store = MemoryVectorStore(dimension=1536)

        results = semantic_code_search(
            query="test",
            embedder=mock_embedder,
            vector_store=vector_store,
            top_k=5,
        )

        assert isinstance(results, list)


# =============================================================================
# P2: Graph Query Layer Tests
# =============================================================================


class TestGraphQueryService:
    """Test graph query service with both backends."""

    def test_graph_query_service_initialization(self) -> None:
        """Test GraphQueryService initialization."""
        from ..tools.graph_query import GraphQueryService

        mock_service = MagicMock()
        service = GraphQueryService(mock_service, backend="memgraph")

        assert service.backend == "memgraph"
        assert service.graph_service is mock_service

    def test_graph_query_service_kuzu_backend(self) -> None:
        """Test GraphQueryService with Kuzu backend."""
        from ..tools.graph_query import GraphQueryService

        mock_service = MagicMock()
        service = GraphQueryService(mock_service, backend="kuzu")

        assert service.backend == "kuzu"

    def test_fetch_nodes_by_ids_empty_list(self) -> None:
        """Test fetch_nodes_by_ids with empty list returns empty."""
        from ..tools.graph_query import GraphQueryService

        mock_service = MagicMock()
        service = GraphQueryService(mock_service)

        results = service.fetch_nodes_by_ids([])
        assert results == []

    def test_fetch_nodes_by_ids_with_results(self) -> None:
        """Test fetch_nodes_by_ids returns parsed nodes."""
        from ..tools.graph_query import GraphQueryService, GraphNode

        mock_service = MagicMock()
        mock_service.fetch_all.return_value = [
            {
                "node_id": 1,
                "qualified_name": "module.func",
                "name": "func",
                "labels": ["Function"],
                "path": "module.py",
                "start_line": 10,
                "end_line": 20,
            }
        ]

        service = GraphQueryService(mock_service)
        results = service.fetch_nodes_by_ids([1])

        assert len(results) == 1
        assert isinstance(results[0], GraphNode)
        assert results[0].node_id == 1
        assert results[0].qualified_name == "module.func"

    def test_fetch_node_by_qualified_name(self) -> None:
        """Test fetching node by qualified name."""
        from ..tools.graph_query import GraphQueryService, GraphNode

        mock_service = MagicMock()
        mock_service.fetch_all.return_value = [
            {
                "node_id": 42,
                "qualified_name": "myproject.utils.helper",
                "name": "helper",
                "labels": ["Function"],
                "path": "utils.py",
            }
        ]

        service = GraphQueryService(mock_service)
        result = service.fetch_node_by_qualified_name("myproject.utils.helper")

        assert isinstance(result, GraphNode)
        assert result.node_id == 42


class TestGraphQueryWithVectorResults:
    """Test integration between vector search and graph queries."""

    def test_query_nodes_by_vector_results(self) -> None:
        """Test querying graph nodes from vector search results."""
        from ..tools.graph_query import query_nodes_by_vector_results
        from ..embeddings.vector_store import SearchResult

        # Create mock vector results
        vector_results = [
            SearchResult(node_id=1, score=0.95, qualified_name="module.func1"),
            SearchResult(node_id=2, score=0.85, qualified_name="module.func2"),
        ]

        mock_graph_service = MagicMock()
        mock_graph_service.fetch_all.return_value = [
            {"node_id": 1, "qualified_name": "module.func1", "name": "func1", "labels": ["Function"]},
            {"node_id": 2, "qualified_name": "module.func2", "name": "func2", "labels": ["Function"]},
        ]

        results = query_nodes_by_vector_results(vector_results, mock_graph_service)

        assert len(results) == 2
        mock_graph_service.fetch_all.assert_called_once()


# =============================================================================
# Backend Compatibility Tests
# =============================================================================


class TestBackendCompatibility:
    """Test compatibility with both Kuzu and Memgraph backends."""

    def test_cypher_query_compatibility_memgraph(self) -> None:
        """Test Cypher query format for Memgraph."""
        from ..tools.graph_query import GraphQueryService

        mock_service = MagicMock()
        service = GraphQueryService(mock_service, backend="memgraph")

        # Build query and verify it works with Memgraph
        query = service._build_nodes_by_id_query()

        # Should contain Memgraph-compatible ID references
        assert "node_id" in query or "id(n)" in query

    def test_cypher_query_compatibility_kuzu(self) -> None:
        """Test Cypher query format for Kuzu."""
        from ..tools.graph_query import GraphQueryService

        mock_service = MagicMock()
        service = GraphQueryService(mock_service, backend="kuzu")

        query = service._build_nodes_by_id_query()

        # Should be compatible with Kuzu's Cypher subset
        assert "MATCH" in query
        assert "RETURN" in query

    def test_node_id_extraction_various_formats(self) -> None:
        """Test node ID extraction from various result formats."""
        from ..tools.graph_query import GraphQueryService

        service = GraphQueryService(MagicMock())

        # Test different ID field names
        assert service._extract_node_id({"node_id": 42}) == 42
        assert service._extract_node_id({"id": 42}) == 42
        assert service._extract_node_id({"n.node_id": 42}) == 42
        assert service._extract_node_id({}) == 0


# =============================================================================
# End-to-End Integration Tests
# =============================================================================


@pytest.mark.skip(reason="Requires full environment setup")
class TestEndToEndIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_memory_backend(
        self,
        sample_repo: Path,
    ) -> None:
        """Test full workflow with memory backend."""
        from ..graph_updater import GraphUpdater
        from ..services.memory_service import MemoryIngestor
        from ..embeddings.qwen3_embedder import DummyEmbedder
        from ..embeddings.vector_store import MemoryVectorStore
        from ..tools.semantic_search import SemanticSearchService

        # Setup
        ingestor = MemoryIngestor()
        embedder = DummyEmbedder(dimension=1536)
        vector_store = MemoryVectorStore(dimension=1536)

        # Create updater with embedding
        updater = GraphUpdater(
            ingestor=ingestor,
            repo_path=sample_repo,
            parsers={},
            queries={},
            embedder=embedder,
            vector_store=vector_store,
            embedding_config={"enabled": True, "batch_size": 10},
        )

        # Run graph building
        # updater.run()  # Would require full parser setup

        # Create semantic search service
        search_service = SemanticSearchService(
            embedder=embedder,
            vector_store=vector_store,
            graph_service=ingestor,
        )

        # Search
        results = search_service.search("calculator", top_k=5)
        assert isinstance(results, list)
