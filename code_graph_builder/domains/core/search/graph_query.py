"""Graph query layer for unified access to Kuzu and Memgraph backends.

This module provides a unified interface for querying graph data from
different backends (Memgraph and Kuzu), enabling seamless integration
with vector store search results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

if TYPE_CHECKING:
    from code_graph_builder.foundation.services import IngestorProtocol, QueryProtocol
    from code_graph_builder.foundation.types.types import ResultRow


@dataclass
class GraphNode:
    """Represents a node in the code graph.

    Attributes:
        node_id: Unique node identifier
        qualified_name: Fully qualified name (e.g., "module.Class.method")
        name: Simple name (e.g., "method")
        type: Node type (Function, Class, Method, Module, etc.)
        path: File path
        start_line: Start line number in file
        end_line: End line number in file
        docstring: Documentation string if available
        properties: Additional node properties
    """

    node_id: int
    qualified_name: str
    name: str
    type: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    docstring: str | None = None
    properties: dict | None = None


@dataclass
class GraphRelationship:
    """Represents a relationship in the code graph.

    Attributes:
        rel_type: Relationship type (CALLS, DEFINES, INHERITS, etc.)
        source_id: Source node ID
        target_id: Target node ID
        properties: Relationship properties
    """

    rel_type: str
    source_id: int
    target_id: int
    properties: dict | None = None


@runtime_checkable
class GraphQueryProtocol(Protocol):
    """Protocol for graph query operations."""

    def fetch_nodes_by_ids(self, node_ids: list[int]) -> list[GraphNode]: ...

    def fetch_node_by_qualified_name(self, qualified_name: str) -> GraphNode | None: ...

    def fetch_functions_by_name(self, name: str) -> list[GraphNode]: ...

    def fetch_callers(self, function_name: str) -> list[GraphNode]: ...

    def fetch_callees(self, function_name: str) -> list[GraphNode]: ...

    def fetch_related_nodes(
        self, node_id: int, relationship_types: list[str] | None = None
    ) -> list[tuple[GraphNode, str]]: ...

    def execute_cypher(self, query: str, params: dict | None = None) -> list[ResultRow]: ...


class GraphQueryService:
    """Unified service for querying code graph data.

    Supports both Memgraph and Kuzu backends through a common interface.

    Example:
        >>> from code_graph_builder.services import MemgraphIngestor
        >>> from code_graph_builder.tools.graph_query import GraphQueryService
        >>>
        >>> with MemgraphIngestor("localhost", 7687) as ingestor:
        ...     query_service = GraphQueryService(ingestor)
        ...     node = query_service.fetch_node_by_qualified_name("myproject.utils.foo")
        ...     callers = query_service.fetch_callers("foo")
    """

    def __init__(self, graph_service: QueryProtocol, backend: str = "memgraph"):
        """Initialize graph query service.

        Args:
            graph_service: Graph service instance (MemgraphIngestor or KuzuIngestor)
            backend: Backend type ("memgraph" or "kuzu")
        """
        self.graph_service = graph_service
        self.backend = backend.lower()

    def fetch_nodes_by_ids(self, node_ids: list[int]) -> list[GraphNode]:
        """Fetch multiple nodes by their IDs.

        Args:
            node_ids: List of node identifiers

        Returns:
            List of GraphNode objects
        """
        if not node_ids:
            return []

        query = self._build_nodes_by_id_query()

        try:
            results = self.graph_service.fetch_all(query, {"node_ids": node_ids})
            return [self._row_to_node(row) for row in results if self._extract_node_id(row) in node_ids]
        except Exception as e:
            logger.error(f"Failed to fetch nodes by IDs: {e}")
            return []

    def fetch_node_by_qualified_name(self, qualified_name: str) -> GraphNode | None:
        """Fetch a single node by its qualified name.

        Args:
            qualified_name: Fully qualified name (e.g., "module.Class.method")

        Returns:
            GraphNode if found, None otherwise
        """
        query = """
            MATCH (n)
            WHERE n.qualified_name = $qualified_name
            RETURN n,
                   n.node_id AS node_id,
                   n.id AS id,
                   n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS labels,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.docstring AS docstring
            LIMIT 1
        """

        try:
            results = self.graph_service.fetch_all(query, {"qualified_name": qualified_name})
            if results:
                return self._row_to_node(results[0])
        except Exception as e:
            logger.error(f"Failed to fetch node {qualified_name}: {e}")

        return None

    def fetch_functions_by_name(self, name: str) -> list[GraphNode]:
        """Find function nodes by name — qualified_name exact match first, then fallback to name match.

        Args:
            name: Function name (simple or qualified)

        Returns:
            List of matching GraphNode objects
        """
        # Try exact match on qualified_name first
        query_qualified = """
            MATCH (n:Function)
            WHERE n.qualified_name = $name
            RETURN n,
                   n.node_id AS node_id,
                   n.id AS id,
                   n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS labels,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.docstring AS docstring
        """

        try:
            results = self.graph_service.fetch_all(query_qualified, {"name": name})
            if results:
                return [self._row_to_node(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to fetch functions by qualified_name '{name}': {e}")
            return []

        # Fallback to name match
        query_name = """
            MATCH (n:Function)
            WHERE n.name = $name
            RETURN n,
                   n.node_id AS node_id,
                   n.id AS id,
                   n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS labels,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.docstring AS docstring
        """

        try:
            results = self.graph_service.fetch_all(query_name, {"name": name})
            return [self._row_to_node(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to fetch functions by name '{name}': {e}")
            return []

    def fetch_callers(self, function_name: str) -> list[GraphNode]:
        """Find all functions that call the given function.

        Args:
            function_name: Function name or qualified name

        Returns:
            List of caller GraphNodes
        """
        # Try qualified name first
        query = """
            MATCH (caller:Function)-[:CALLS]->(callee)
            WHERE callee.qualified_name = $name
               OR callee.name = $name
            RETURN caller,
                   caller.node_id AS node_id,
                   caller.id AS id,
                   caller.qualified_name AS qualified_name,
                   caller.name AS name,
                   labels(caller) AS labels,
                   caller.path AS path,
                   caller.start_line AS start_line,
                   caller.end_line AS end_line
        """

        try:
            results = self.graph_service.fetch_all(query, {"name": function_name})
            return [self._row_to_node(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to fetch callers of {function_name}: {e}")
            return []

    def fetch_callers_with_rel_props(
        self, function_name: str
    ) -> list[tuple[GraphNode, dict]]:
        """Find all callers with CALLS relationship properties.

        Returns a list of ``(caller_node, rel_properties)`` tuples.
        ``rel_properties`` may contain ``{"indirect": True, "via_field": "..."}``
        for function-pointer-based calls.
        """
        query = """
            MATCH (caller:Function)-[r:CALLS]->(callee)
            WHERE callee.qualified_name = $name
               OR callee.name = $name
            RETURN caller,
                   caller.node_id AS node_id,
                   caller.id AS id,
                   caller.qualified_name AS qualified_name,
                   caller.name AS name,
                   labels(caller) AS labels,
                   caller.path AS path,
                   caller.start_line AS start_line,
                   caller.end_line AS end_line,
                   r.indirect AS indirect,
                   r.via_field AS via_field
        """

        try:
            results = self.graph_service.fetch_all(query, {"name": function_name})
            output: list[tuple[GraphNode, dict]] = []
            for row in results:
                node = self._row_to_node(row)
                rel_props: dict = {}
                if row.get("indirect"):
                    rel_props["indirect"] = True
                if row.get("via_field"):
                    rel_props["via_field"] = str(row["via_field"])
                output.append((node, rel_props))
            return output
        except Exception as e:
            logger.error(f"Failed to fetch callers with props of {function_name}: {e}")
            return []

    def fetch_callees(self, function_name: str) -> list[GraphNode]:
        """Find all functions called by the given function.

        Args:
            function_name: Function name or qualified name

        Returns:
            List of callee GraphNodes
        """
        query = """
            MATCH (caller:Function)-[:CALLS]->(callee:Function)
            WHERE caller.qualified_name = $name
               OR caller.name = $name
            RETURN callee,
                   callee.node_id AS node_id,
                   callee.id AS id,
                   callee.qualified_name AS qualified_name,
                   callee.name AS name,
                   labels(callee) AS labels,
                   callee.path AS path,
                   callee.start_line AS start_line,
                   callee.end_line AS end_line
        """

        try:
            results = self.graph_service.fetch_all(query, {"name": function_name})
            return [self._row_to_node(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to fetch callees of {function_name}: {e}")
            return []

    def fetch_related_nodes(
        self,
        node_id: int,
        relationship_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[tuple[GraphNode, str]]:
        """Fetch nodes related to the given node.

        Args:
            node_id: Node identifier
            relationship_types: Optional filter for relationship types
            direction: Relationship direction ("in", "out", or "both")

        Returns:
            List of (GraphNode, relationship_type) tuples
        """
        if direction == "in":
            pattern = "(related)-[r]->(n)"
        elif direction == "out":
            pattern = "(n)-[r]->(related)"
        else:
            pattern = "(n)-[r]-(related)"

        rel_filter = ""
        if relationship_types:
            rel_types = "|".join(f":{rt}" for rt in relationship_types)
            rel_filter = f"AND type(r) IN {relationship_types}"

        query = f"""
            MATCH {pattern}
            WHERE n.node_id = $node_id
               OR n.id = $node_id
               OR id(n) = $node_id
            {rel_filter}
            RETURN related,
                   related.node_id AS node_id,
                   related.id AS id,
                   related.qualified_name AS qualified_name,
                   related.name AS name,
                   labels(related) AS labels,
                   related.path AS path,
                   related.start_line AS start_line,
                   related.end_line AS end_line,
                   type(r) AS rel_type
        """

        try:
            results = self.graph_service.fetch_all(query, {"node_id": node_id})
            return [
                (self._row_to_node(row), str(row.get("rel_type", "UNKNOWN")))
                for row in results
            ]
        except Exception as e:
            logger.error(f"Failed to fetch related nodes for {node_id}: {e}")
            return []

    def fetch_class_hierarchy(self, class_name: str) -> dict:
        """Fetch class hierarchy information.

        Args:
            class_name: Class name or qualified name

        Returns:
            Dictionary with superclass and subclasses
        """
        query = """
            MATCH (c:Class)
            WHERE c.qualified_name = $name OR c.name = $name
            OPTIONAL MATCH (c)-[:INHERITS]->(super:Class)
            OPTIONAL MATCH (sub:Class)-[:INHERITS]->(c)
            RETURN c,
                   super.qualified_name AS superclass,
                   collect(sub.qualified_name) AS subclasses
        """

        try:
            results = self.graph_service.fetch_all(query, {"name": class_name})
            if results:
                return {
                    "class": results[0].get("c"),
                    "superclass": results[0].get("superclass"),
                    "subclasses": results[0].get("subclasses", []),
                }
        except Exception as e:
            logger.error(f"Failed to fetch class hierarchy for {class_name}: {e}")

        return {}

    def execute_cypher(self, query: str, params: dict | None = None) -> list[ResultRow]:
        """Execute a raw Cypher query.

        Args:
            query: Cypher query string
            params: Query parameters

        Returns:
            Query results as list of dictionaries
        """
        try:
            return self.graph_service.fetch_all(query, params or {})
        except Exception as e:
            logger.error(f"Cypher query failed: {e}")
            return []

    def _build_nodes_by_id_query(self) -> str:
        """Build query to fetch nodes by IDs.

        Compatible with both Memgraph and Kuzu.
        """
        return """
            MATCH (n)
            WHERE n.node_id IN $node_ids
               OR n.id IN $node_ids
               OR id(n) IN $node_ids
            RETURN n,
                   n.node_id AS node_id,
                   n.id AS id,
                   n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS labels,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.docstring AS docstring
        """

    def _extract_node_id(self, row: ResultRow) -> int:
        """Extract node ID from query result."""
        for key in ["node_id", "id", "n.node_id", "n.id"]:
            if key in row:
                val = row[key]
                if isinstance(val, int):
                    return val
                try:
                    return int(val)
                except (ValueError, TypeError):
                    continue
        return 0

    def _extract_type(self, row: ResultRow) -> str:
        """Extract node type from query result."""
        # Try labels first
        labels = row.get("labels")
        if labels:
            if isinstance(labels, list) and labels:
                return labels[0]
            return str(labels)

        # Try type property
        node_type = row.get("type")
        if node_type:
            return str(node_type)

        # Extract from node object if available
        node = row.get("n") or row.get("caller") or row.get("callee") or row.get("related")
        if node and isinstance(node, dict):
            node_labels = node.get("_label") or node.get("labels")
            if node_labels:
                if isinstance(node_labels, list) and node_labels:
                    return node_labels[0]
                return str(node_labels)
            node_type = node.get("type")
            if node_type:
                return str(node_type)

        return "Unknown"

    def _row_to_node(self, row: ResultRow) -> GraphNode:
        """Convert query result row to GraphNode."""
        node_id = self._extract_node_id(row)
        qualified_name = str(row.get("qualified_name", ""))
        name = str(row.get("name", qualified_name.split(".")[-1] if qualified_name else ""))
        node_type = self._extract_type(row)

        return GraphNode(
            node_id=node_id,
            qualified_name=qualified_name,
            name=name,
            type=node_type,
            path=str(row.get("path")) if row.get("path") else None,
            start_line=int(row["start_line"]) if row.get("start_line") is not None else None,
            end_line=int(row["end_line"]) if row.get("end_line") is not None else None,
            docstring=str(row["docstring"]) if row.get("docstring") else None,
            properties={k: v for k, v in row.items() if k not in [
                "node_id", "id", "qualified_name", "name", "labels", "type",
                "path", "start_line", "end_line", "docstring"
            ]},
        )


# Convenience factory functions


def create_graph_query_service(
    graph_service: QueryProtocol,
    backend: str = "memgraph",
) -> GraphQueryService:
    """Create graph query service with auto-detected backend.

    Args:
        graph_service: Graph service instance
        backend: Backend type ("memgraph" or "kuzu")

    Returns:
        Configured GraphQueryService
    """
    return GraphQueryService(graph_service, backend)


def query_nodes_by_vector_results(
    vector_results: list,
    graph_service: QueryProtocol,
) -> list[GraphNode]:
    """Query graph nodes corresponding to vector search results.

    This is the main integration point between vector store and graph database.

    Args:
        vector_results: Results from VectorStore.search_similar()
        graph_service: Graph service to query

    Returns:
        List of GraphNode objects
    """
    service = GraphQueryService(graph_service)
    node_ids = [vr.node_id for vr in vector_results]
    return service.fetch_nodes_by_ids(node_ids)


def get_function_with_context(
    qualified_name: str,
    graph_service: QueryProtocol,
    include_callers: bool = True,
    include_callees: bool = True,
) -> dict:
    """Get comprehensive information about a function including its context.

    Args:
        qualified_name: Function qualified name
        graph_service: Graph service
        include_callers: Whether to include calling functions
        include_callees: Whether to include called functions

    Returns:
        Dictionary with function info, callers, and callees
    """
    service = GraphQueryService(graph_service)

    result = {
        "function": None,
        "callers": [],
        "callees": [],
        "related": [],
    }

    # Get main function
    func = service.fetch_node_by_qualified_name(qualified_name)
    if func:
        result["function"] = func

    # Get callers
    if include_callers:
        result["callers"] = service.fetch_callers(qualified_name)

    # Get callees
    if include_callees:
        result["callees"] = service.fetch_callees(qualified_name)

    return result
