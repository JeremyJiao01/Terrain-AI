"""Tools for Code Graph Builder.

This module provides tools for semantic code search and retrieval,
compatible with CAMEL Agent framework.
"""

from __future__ import annotations

# Semantic search tools
from .semantic_search import (
    SemanticSearchResult,
    SemanticSearchService,
    create_semantic_search_service,
    get_function_source_by_node_id,
    semantic_code_search,
)

# Graph query tools
from .graph_query import (
    GraphNode,
    GraphQueryService,
    create_graph_query_service,
    get_function_with_context,
    query_nodes_by_vector_results,
)

__all__ = [
    # Semantic search
    "semantic_code_search",
    "get_function_source_by_node_id",
    "create_semantic_search_service",
    "SemanticSearchService",
    "SemanticSearchResult",
    # Graph query
    "GraphNode",
    "GraphQueryService",
    "create_graph_query_service",
    "get_function_with_context",
    "query_nodes_by_vector_results",
]
