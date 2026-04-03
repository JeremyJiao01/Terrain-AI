"""Semantic search tools for code using embeddings.

This module provides tools for semantic code search using vector embeddings.
Integrates with both Memgraph and Kuzu backends for graph data retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from ..embeddings.qwen3_embedder import BaseEmbedder
from ..embeddings.vector_store import SearchResult, VectorStore

if TYPE_CHECKING:
    from ..services import IngestorProtocol, QueryProtocol
    from ..types import ResultRow


@dataclass
class SemanticSearchResult:
    """Result from semantic code search.

    Attributes:
        node_id: Node identifier in graph database
        qualified_name: Fully qualified name of code entity
        name: Simple name of the entity
        type: Entity type (Function, Class, Method, etc.)
        score: Similarity score (0-1)
        source_code: Source code if available
        file_path: File path if available
        start_line: Start line number
        end_line: End line number
    """

    node_id: int
    qualified_name: str
    name: str
    type: str
    score: float
    source_code: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@runtime_checkable
class GraphServiceProtocol(Protocol):
    """Protocol for graph service operations needed by semantic search."""

    def fetch_all(self, query: str, params: dict | None = None) -> list[ResultRow]: ...

    def query(self, cypher: str, params: dict | None = None) -> list[ResultRow]: ...


class SemanticSearchService:
    """Service for semantic code search.

    Combines vector similarity search with graph database queries
    to provide rich semantic search capabilities.

    Example:
        >>> from code_graph_builder.embeddings import create_embedder, create_vector_store
        >>> from code_graph_builder.services import MemgraphIngestor
        >>> from code_graph_builder.tools.semantic_search import SemanticSearchService
        >>>
        >>> embedder = create_embedder()
        >>> vector_store = create_vector_store(backend="memory", dimension=1536)
        >>>
        >>> with MemgraphIngestor("localhost", 7687) as ingestor:
        ...     service = SemanticSearchService(
        ...         embedder=embedder,
        ...         vector_store=vector_store,
        ...         graph_service=ingestor
        ...     )
        ...     results = service.search("recursive fibonacci implementation", top_k=5)
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: VectorStore,
        graph_service: GraphServiceProtocol | None = None,
    ):
        """Initialize semantic search service.

        Args:
            embedder: Embedder for generating query embeddings
            vector_store: Vector store for similarity search
            graph_service: Optional graph service for retrieving full node data
        """
        self.embedder = embedder
        self.vector_store = vector_store
        self.graph_service = graph_service

    def search(
        self,
        query: str,
        top_k: int = 5,
        entity_types: list[str] | None = None,
    ) -> list[SemanticSearchResult]:
        """Search for code semantically similar to the query.

        Uses a hybrid approach: vector similarity provides the base score,
        then keyword matching boosts results whose names, signatures, or
        qualified names contain the query terms.  This mitigates the
        sensitivity of pure embedding search to minor query variations
        (e.g. "pv 拉短" vs "拉短").

        Args:
            query: Natural language query describing what to find
            top_k: Number of results to return
            entity_types: Optional filter for entity types (Function, Class, etc.)

        Returns:
            List of semantic search results
        """
        try:
            # Generate query embedding
            query_embedding = self.embedder.embed_query(query)

            # Search vector store — fetch a larger candidate set so keyword
            # boosting can promote relevant results that ranked lower.
            candidate_k = max(top_k * 4, 20)

            filter_metadata = None
            if entity_types:
                filter_metadata = {"type": entity_types[0]} if len(entity_types) == 1 else None

            vector_results = self.vector_store.search_similar(
                query_embedding, top_k=candidate_k, filter_metadata=filter_metadata
            )

            if not vector_results:
                return []

            # --- Keyword boost re-ranking ---
            vector_results = self._keyword_boost(query, vector_results, top_k)

            # Enrich with graph data if available
            if self.graph_service:
                return self._enrich_results_from_graph(vector_results)
            else:
                return self._convert_results(vector_results)

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Keyword boost helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        """Split query into searchable tokens.

        Handles mixed Chinese/English text by splitting on whitespace and
        punctuation, then keeping non-empty tokens of length >= 2.
        """
        import re
        # Split on whitespace and common punctuation
        raw = re.split(r'[\s,，。、；;:：!！?？()\[\]{}]+', query.lower())
        return [t for t in raw if len(t) >= 2]

    def _keyword_boost(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        """Re-rank results by blending vector score with keyword matches.

        For each result, check whether any query token appears in the
        record's ``qualified_name`` or metadata fields (``name``,
        ``signature``, ``module``).  Matching tokens contribute a small
        additive bonus so that keyword-relevant results float up without
        completely overriding semantic similarity.
        """
        tokens = self._tokenize_query(query)
        if not tokens:
            return results[:top_k]

        _KEYWORD_WEIGHT = 0.05  # bonus per matched token (additive)

        boosted: list[tuple[float, SearchResult]] = []
        for r in results:
            bonus = 0.0
            # Gather text fields to match against
            _get = getattr(self.vector_store, "get_embedding", None)
            record = _get(r.node_id) if _get else None
            searchable = r.qualified_name.lower()
            if record and record.metadata:
                for field in ("name", "signature", "module"):
                    val = record.metadata.get(field)
                    if isinstance(val, str):
                        searchable += " " + val.lower()

            for token in tokens:
                if token in searchable:
                    bonus += _KEYWORD_WEIGHT

            boosted.append((r.score + bonus, r))

        boosted.sort(key=lambda x: x[0], reverse=True)

        # Rebuild SearchResult list with adjusted scores
        return [
            SearchResult(
                node_id=sr.node_id,
                score=round(adjusted, 4),
                qualified_name=sr.qualified_name,
            )
            for adjusted, sr in boosted[:top_k]
        ]

    def _convert_results(self, vector_results: list[SearchResult]) -> list[SemanticSearchResult]:
        """Convert vector search results to semantic search results."""
        results: list[SemanticSearchResult] = []

        for vr in vector_results:
            # Extract name from qualified name
            name = vr.qualified_name.split(".")[-1] if "." in vr.qualified_name else vr.qualified_name

            results.append(
                SemanticSearchResult(
                    node_id=vr.node_id,
                    qualified_name=vr.qualified_name,
                    name=name,
                    type="Unknown",
                    score=vr.score,
                )
            )

        return results

    def _enrich_results_from_graph(
        self, vector_results: list[SearchResult]
    ) -> list[SemanticSearchResult]:
        """Enrich vector search results with data from graph database."""
        if not self.graph_service:
            return self._convert_results(vector_results)

        qnames = [vr.qualified_name for vr in vector_results]
        query = self._build_nodes_query(qnames)

        try:
            graph_results = self.graph_service.fetch_all(query, {"qnames": qnames})
            graph_data_map = {
                row.get("qualified_name", ""): row for row in graph_results
            }
        except Exception as e:
            logger.warning(f"Failed to enrich results from graph: {e}")
            return self._convert_results(vector_results)

        results: list[SemanticSearchResult] = []
        for vr in vector_results:
            graph_data = graph_data_map.get(vr.qualified_name, {})
            name = graph_data.get("name") or (
                vr.qualified_name.split(".")[-1]
                if "." in vr.qualified_name
                else vr.qualified_name
            )
            results.append(
                SemanticSearchResult(
                    node_id=vr.node_id,
                    qualified_name=vr.qualified_name,
                    name=name,
                    type=graph_data.get("type", "Unknown"),
                    score=vr.score,
                    source_code=graph_data.get("source_code"),
                    file_path=graph_data.get("path") or None,
                    start_line=graph_data.get("start_line"),
                    end_line=graph_data.get("end_line"),
                )
            )
        return results

    def _build_nodes_query(self, qualified_names: list[str]) -> str:
        """Build Cypher query to fetch node details by qualified names."""
        return """
            MATCH (m:Module)-[:DEFINES]->(f:Function)
            WHERE f.qualified_name IN $qnames
            RETURN DISTINCT f.qualified_name AS qualified_name,
                   f.name AS name,
                   m.path AS path,
                   f.start_line AS start_line,
                   f.end_line AS end_line
        """

    def get_source_code(self, node_id: int) -> str | None:
        """Get source code for a specific node by ID.

        Args:
            node_id: Node identifier

        Returns:
            Source code string or None if not found
        """
        if not self.graph_service:
            return None

        query = """
            MATCH (n)
            WHERE n.node_id = $node_id
               OR n.id = $node_id
               OR id(n) = $node_id
            RETURN n.source_code AS source_code,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line
        """

        try:
            results = self.graph_service.fetch_all(query, {"node_id": node_id})
            if results:
                return str(results[0].get("source_code", "")) or None
        except Exception as e:
            logger.warning(f"Failed to get source code for node {node_id}: {e}")

        return None

    def get_source_from_file(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        repo_path: Path | None = None,
    ) -> str | None:
        """Extract source code from file.

        Args:
            file_path: Path to the file
            start_line: Start line (1-indexed)
            end_line: End line (inclusive)
            repo_path: Repository root path for resolving relative paths

        Returns:
            Source code string or None if extraction fails
        """
        try:
            path = Path(file_path)
            if repo_path and not path.is_absolute():
                path = repo_path / path

            if not path.exists():
                return None

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            # Adjust for 1-indexed lines
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)

            return "".join(lines[start_idx:end_idx])

        except Exception as e:
            logger.debug(f"Failed to extract source from {file_path}: {e}")
            return None


def create_semantic_search_service(
    embedder: BaseEmbedder,
    vector_store: VectorStore,
    graph_service: GraphServiceProtocol | None = None,
) -> SemanticSearchService:
    """Factory function to create semantic search service.

    Args:
        embedder: Embedder instance
        vector_store: Vector store instance
        graph_service: Optional graph service for data enrichment

    Returns:
        Configured SemanticSearchService
    """
    return SemanticSearchService(
        embedder=embedder,
        vector_store=vector_store,
        graph_service=graph_service,
    )


# Convenience functions for direct use


def semantic_code_search(
    query: str,
    embedder: BaseEmbedder,
    vector_store: VectorStore,
    graph_service: GraphServiceProtocol | None = None,
    top_k: int = 5,
) -> list[SemanticSearchResult]:
    """Perform semantic code search.

    Convenience function for one-off searches without creating a service.

    Args:
        query: Natural language query
        embedder: Embedder for query encoding
        vector_store: Vector store to search
        graph_service: Optional graph service for enrichment
        top_k: Number of results

    Returns:
        List of search results
    """
    service = SemanticSearchService(
        embedder=embedder,
        vector_store=vector_store,
        graph_service=graph_service,
    )
    return service.search(query, top_k=top_k)


def get_function_source_by_node_id(
    node_id: int,
    graph_service: GraphServiceProtocol,
    repo_path: Path | None = None,
) -> str | None:
    """Get function source code by node ID.

    Args:
        node_id: Node identifier
        graph_service: Graph service to query
        repo_path: Repository path for file resolution

    Returns:
        Source code or None
    """
    service = SemanticSearchService(
        embedder=None,  # Not needed for this operation
        vector_store=None,
        graph_service=graph_service,
    )

    # Try to get from graph first
    source = service.get_source_code(node_id)
    if source:
        return source

    # Try to get from file
    query = """
        MATCH (n)
        WHERE n.node_id = $node_id OR n.id = $node_id OR id(n) = $node_id
        RETURN n.path AS path, n.start_line AS start_line, n.end_line AS end_line
    """

    try:
        results = graph_service.fetch_all(query, {"node_id": node_id})
        if results and repo_path:
            row = results[0]
            return service.get_source_from_file(
                str(row.get("path", "")),
                int(row.get("start_line", 0)) if row.get("start_line") else 0,
                int(row.get("end_line", 0)) if row.get("end_line") else 0,
                repo_path,
            )
    except Exception as e:
        logger.warning(f"Failed to get source for node {node_id}: {e}")

    return None
