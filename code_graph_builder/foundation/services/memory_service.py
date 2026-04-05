"""Memory-only graph service - No database required."""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

from loguru import logger

from code_graph_builder.foundation.types.types import GraphData, PropertyDict, PropertyValue, ResultRow


class MemoryIngestor:
    """Ingestor that stores graph data in memory only (no persistence).

    This is useful for testing and one-off analysis where database
    persistence is not needed.

    Example:
        >>> ingestor = MemoryIngestor()
        >>> with ingestor:
        ...     ingestor.ensure_node_batch("Function", {"name": "foo"})
        ...     ingestor.flush_all()
        >>> data = ingestor.export_graph()
    """

    def __init__(self):
        """Initialize memory ingestor."""
        self.nodes: list[dict] = []
        self.relationships: list[dict] = []
        self._node_buffer: list[tuple[str, PropertyDict]] = []
        self._rel_buffer: list[tuple] = []
        self._batch_size = 1000

    def __enter__(self) -> MemoryIngestor:
        """Enter context manager."""
        logger.info("Memory ingestor initialized (no persistence)")
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit context manager."""
        self.flush_all()
        if exc_type:
            logger.exception(f"Exception during ingest: {exc_val}")

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        """Add a node to the batch buffer."""
        self._node_buffer.append((label, properties.copy()))
        if len(self._node_buffer) >= self._batch_size:
            self.flush_nodes()

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, PropertyValue],
        rel_type: str,
        target: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None:
        """Add a relationship to the batch buffer."""
        self._rel_buffer.append((source, rel_type, target, properties))
        if len(self._rel_buffer) >= self._batch_size:
            self.flush_relationships()

    def flush_nodes(self) -> None:
        """Flush node buffer to memory."""
        for label, props in self._node_buffer:
            self.nodes.append({
                "label": label,
                "properties": props,
                "id": len(self.nodes),
            })
        logger.debug(f"Flushed {len(self._node_buffer)} nodes to memory")
        self._node_buffer = []

    def flush_relationships(self) -> None:
        """Flush relationship buffer to memory."""
        for source, rel_type, target, props in self._rel_buffer:
            self.relationships.append({
                "source": {"label": source[0], "key": source[1], "value": source[2]},
                "type": rel_type,
                "target": {"label": target[0], "key": target[1], "value": target[2]},
                "properties": props or {},
            })
        logger.debug(f"Flushed {len(self._rel_buffer)} relationships to memory")
        self._rel_buffer = []

    def flush_all(self) -> None:
        """Flush all pending data."""
        self.flush_nodes()
        self.flush_relationships()

    def clean_database(self) -> None:
        """Clean all data from memory."""
        self.nodes = []
        self.relationships = []
        self._node_buffer = []
        self._rel_buffer = []
        logger.info("Memory database cleaned")

    def export_graph(self) -> GraphData:
        """Export the graph data."""
        return {
            "nodes": self.nodes,
            "relationships": self.relationships,
            "metadata": {
                "total_nodes": len(self.nodes),
                "total_relationships": len(self.relationships),
            },
        }

    def export_graph_to_dict(self) -> GraphData:
        """Export the graph data (alias for export_graph)."""
        return self.export_graph()

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about the graph."""
        # Count node labels
        node_labels: dict[str, int] = {}
        for node in self.nodes:
            label = node.get("label", "Unknown")
            node_labels[label] = node_labels.get(label, 0) + 1

        # Count relationship types
        rel_types: dict[str, int] = {}
        for rel in self.relationships:
            rel_type = rel.get("type", "UNKNOWN")
            rel_types[rel_type] = rel_types.get(rel_type, 0) + 1

        return {
            "node_count": len(self.nodes),
            "relationship_count": len(self.relationships),
            "node_labels": node_labels,
            "relationship_types": rel_types,
        }

    def query(self, cypher_query: str, params: PropertyDict | None = None) -> list[ResultRow]:
        """Execute a query against the in-memory graph.

        Note: This is a simplified implementation that only supports
        basic MATCH queries.
        """
        results: list[ResultRow] = []

        # Very basic query parsing - just return all nodes for MATCH (n)
        if "MATCH (n)" in cypher_query and "count" not in cypher_query.lower():
            for node in self.nodes:
                results.append({"n": node})

        return results

    def save_to_file(self, filepath: str | Path) -> None:
        """Save the graph data to a JSON file."""
        data = self.export_graph()
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Graph saved to {filepath}")

    def load_from_file(self, filepath: str | Path) -> None:
        """Load graph data from a JSON file."""
        with open(filepath) as f:
            data = json.load(f)
        self.nodes = data.get("nodes", [])
        self.relationships = data.get("relationships", [])
        logger.info(f"Graph loaded from {filepath}")
