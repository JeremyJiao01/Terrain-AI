"""Graph service for connecting to and interacting with Memgraph."""

from __future__ import annotations

import types
from collections import defaultdict
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger

from code_graph_builder.foundation.types.types import (
    BatchParams,
    BatchWrapper,
    ColumnDescriptor,
    CursorProtocol,
    GraphData,
    GraphMetadata,
    GraphNode,
    NodeBatchRow,
    PropertyDict,
    PropertyValue,
    RelBatchRow,
    ResultRow,
    ResultValue,
)

if TYPE_CHECKING:
    import mgclient


class MemgraphIngestor:
    """Ingestor for writing code graph data to Memgraph."""

    def __init__(self, host: str, port: int, batch_size: int = 1000):
        self._host = host
        self._port = port
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.batch_size = batch_size
        self.conn: mgclient.Connection | None = None
        self.node_buffer: list[tuple[str, dict[str, PropertyValue]]] = []
        self.relationship_buffer: list[
            tuple[
                tuple[str, str, PropertyValue],
                str,
                tuple[str, str, PropertyValue],
                dict[str, PropertyValue] | None,
            ]
        ] = []

    def __enter__(self) -> MemgraphIngestor:
        import mgclient

        logger.info(f"Connecting to Memgraph at {self._host}:{self._port}")
        self.conn = mgclient.connect(host=self._host, port=self._port)
        self.conn.autocommit = True
        logger.info("Connected to Memgraph")
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if exc_type:
            logger.exception(f"Exception during ingest: {exc_val}")
            try:
                self.flush_all()
            except Exception as flush_err:
                logger.error(f"Flush error during exception handling: {flush_err}")
        else:
            self.flush_all()
        if self.conn:
            self.conn.close()
            logger.info("Disconnected from Memgraph")

    @contextmanager
    def _get_cursor(self) -> Generator[CursorProtocol, None, None]:
        if not self.conn:
            raise ConnectionError("Not connected to database")
        cursor: CursorProtocol | None = None
        try:
            cursor = self.conn.cursor()
            yield cursor
        finally:
            if cursor:
                cursor.close()

    def _cursor_to_results(self, cursor: CursorProtocol) -> list[ResultRow]:
        if not cursor.description:
            return []
        column_names = [desc.name for desc in cursor.description]
        return [
            dict[str, ResultValue](zip(column_names, row)) for row in cursor.fetchall()
        ]

    def _execute_query(
        self,
        query: str,
        params: dict[str, PropertyValue] | None = None,
    ) -> list[ResultRow]:
        params = params or {}
        with self._get_cursor() as cursor:
            try:
                cursor.execute(query, params)
                return self._cursor_to_results(cursor)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.error(f"Query error: {e}")
                    logger.error(f"Query: {query}")
                    logger.error(f"Params: {params}")
                raise

    def _execute_batch(self, query: str, params_list: Sequence[BatchParams]) -> None:
        if not self.conn or not params_list:
            return
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                f"UNWIND $batch AS row\n{query}",
                BatchWrapper(batch=params_list),
            )
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.error(f"Batch error: {e}")
                logger.error(f"Query: {query}")
        finally:
            if cursor:
                cursor.close()

    def fetch_all(
        self,
        query: str,
        params: dict[str, PropertyValue] | None = None,
    ) -> list[ResultRow]:
        """Execute a query and return all results."""
        return self._execute_query(query, params)

    def ensure_node_batch(
        self,
        label: str,
        id_key: str,
        id_val: PropertyValue,
        props: dict[str, PropertyValue],
    ) -> None:
        """Queue a node for batch insertion."""
        unique_id = f"{label}:{id_key}:{id_val}"
        self.node_buffer.append((unique_id, {"label": label, "id_key": id_key, "id_val": id_val, **props}))
        if len(self.node_buffer) >= self.batch_size:
            self.flush_nodes()

    def ensure_relationship_batch(
        self,
        from_label: str,
        from_key: str,
        from_val: PropertyValue,
        rel_type: str,
        to_label: str,
        to_key: str,
        to_val: PropertyValue,
        props: dict[str, PropertyValue] | None = None,
    ) -> None:
        """Queue a relationship for batch insertion."""
        from_id = (from_label, from_key, from_val)
        to_id = (to_label, to_key, to_val)
        self.relationship_buffer.append((from_id, rel_type, to_id, props))
        if len(self.relationship_buffer) >= self.batch_size:
            self.flush_relationships()

    def flush_nodes(self) -> None:
        """Flush buffered nodes to the database."""
        if not self.node_buffer:
            return

        # Group by label for efficient batching
        by_label: defaultdict[str, list[dict]] = defaultdict(list)
        for _unique_id, node_data in self.node_buffer:
            label = node_data.pop("label")
            by_label[label].append(node_data)

        for label, nodes in by_label.items():
            query = f"""
            UNWIND $batch AS row
            MERGE (n:{label} {{{nodes[0].get('id_key', 'id')}: row.id_val}})
            SET n += row
            """
            self._execute_batch(query, nodes)

        logger.debug(f"Flushed {len(self.node_buffer)} nodes")
        self.node_buffer.clear()

    def flush_relationships(self) -> None:
        """Flush buffered relationships to the database."""
        if not self.relationship_buffer:
            return

        # Group by type for efficient batching
        by_type: defaultdict[
            str,
            list[dict],
        ] = defaultdict(list)
        for (from_label, from_key, from_val), rel_type, (to_label, to_key, to_val), props in self.relationship_buffer:
            row: dict = {
                "from_label": from_label,
                "from_key": from_key,
                "from_val": from_val,
                "to_label": to_label,
                "to_key": to_key,
                "to_val": to_val,
            }
            if props:
                row["props"] = props
            by_type[rel_type].append(row)

        for rel_type, rels in by_type.items():
            query = f"""
            UNWIND $batch AS row
            MATCH (a {{{rels[0].get('from_key', 'id')}: row.from_val}})
            MATCH (b {{{rels[0].get('to_key', 'id')}: row.to_val}})
            MERGE (a)-[r:{rel_type}]->(b)
            SET r += row.props
            """
            self._execute_batch(query, rels)

        logger.debug(f"Flushed {len(self.relationship_buffer)} relationships")
        self.relationship_buffer.clear()

    def flush_all(self) -> None:
        """Flush all buffered data to the database."""
        self.flush_nodes()
        self.flush_relationships()

    def clean_database(self) -> None:
        """Delete all data from the database."""
        logger.warning("Cleaning database - deleting all nodes and relationships")
        self._execute_query("MATCH (n) DETACH DELETE n;")
        logger.info("Database cleaned")

    def list_projects(self) -> list[str]:
        """List all projects in the database."""
        results = self._execute_query(
            "MATCH (p:Project) RETURN p.name AS name ORDER BY p.name"
        )
        return [row["name"] for row in results if row.get("name")]

    def delete_project(self, project_name: str) -> None:
        """Delete a project and all its related data."""
        logger.info(f"Deleting project: {project_name}")
        query = """
        MATCH (p:Project {name: $project_name})
        OPTIONAL MATCH (p)-[:CONTAINS_PACKAGE|CONTAINS_FOLDER|CONTAINS_FILE|CONTAINS_MODULE*]->(container)
        OPTIONAL MATCH (container)-[:DEFINES|DEFINES_METHOD*]->(defined)
        DETACH DELETE p, container, defined
        """
        self._execute_query(query, {"project_name": project_name})
        logger.info(f"Project {project_name} deleted")

    def export_graph_to_dict(self) -> GraphData:
        """Export the entire graph as a dictionary."""
        logger.info("Exporting graph to dictionary")

        nodes_query = """
        MATCH (n)
        RETURN id(n) as node_id, labels(n) as labels, properties(n) as properties
        """
        nodes = self._execute_query(nodes_query)

        rels_query = """
        MATCH (a)-[r]->(b)
        RETURN id(a) as from_id, id(b) as to_id, type(r) as type, properties(r) as properties
        """
        relationships = self._execute_query(rels_query)

        metadata = GraphMetadata(
            total_nodes=len(nodes),
            total_relationships=len(relationships),
            exported_at=datetime.now(UTC).isoformat(),
        )

        logger.info(
            f"Exported {len(nodes)} nodes and {len(relationships)} relationships"
        )

        return GraphData(
            nodes=nodes,
            relationships=relationships,
            metadata=metadata,
        )

    def get_node_by_id(self, node_id: int) -> GraphNode | None:
        """Get a node by its internal ID.

        Args:
            node_id: Memgraph internal node ID

        Returns:
            GraphNode if found, None otherwise
        """
        query = """
        MATCH (n)
        WHERE id(n) = $node_id
        RETURN id(n) as node_id, labels(n) as labels, properties(n) as props
        """
        results = self._execute_query(query, {"node_id": node_id})

        if not results:
            return None

        row = results[0]
        return self._row_to_graph_node(row)

    def get_nodes_by_ids(self, node_ids: list[int]) -> list[GraphNode]:
        """Get multiple nodes by their internal IDs.

        Args:
            node_ids: List of Memgraph internal node IDs

        Returns:
            List of GraphNode objects
        """
        if not node_ids:
            return []

        query = """
        MATCH (n)
        WHERE id(n) IN $node_ids
        RETURN id(n) as node_id, labels(n) as labels, properties(n) as props
        """
        results = self._execute_query(query, {"node_ids": node_ids})

        return [self._row_to_graph_node(row) for row in results if row]

    def search_nodes(
        self,
        query_str: str,
        label: str | None = None,
        limit: int = 10,
    ) -> list[GraphNode]:
        """Search nodes by name or qualified name.

        Args:
            query_str: Search query string
            label: Optional node label filter
            limit: Maximum number of results

        Returns:
            List of matching GraphNode objects
        """
        if label:
            cypher = """
            MATCH (n:$label)
            WHERE n.name CONTAINS $query OR n.qualified_name CONTAINS $query
            RETURN id(n) as node_id, labels(n) as labels, properties(n) as props
            LIMIT $limit
            """
            cypher = cypher.replace("$label", label)
        else:
            cypher = """
            MATCH (n)
            WHERE n.name CONTAINS $query OR n.qualified_name CONTAINS $query
            RETURN id(n) as node_id, labels(n) as labels, properties(n) as props
            LIMIT $limit
            """

        results = self._execute_query(
            cypher, {"query": query_str, "limit": limit}
        )

        return [self._row_to_graph_node(row) for row in results if row]

    def get_node_relationships(
        self,
        node_id: int,
        rel_type: str | None = None,
        direction: str = "both",
    ) -> list[tuple[GraphNode, str, str]]:
        """Get relationships for a node.

        Args:
            node_id: Node identifier
            rel_type: Optional relationship type filter
            direction: "out", "in", or "both"

        Returns:
            List of (related_node, relationship_type, direction) tuples
        """
        results = []

        if direction in ("out", "both"):
            if rel_type:
                query = f"""
                MATCH (n)-[r:{rel_type}]->(m)
                WHERE id(n) = $node_id
                RETURN id(m) as node_id, labels(m) as labels, properties(m) as props,
                       type(r) as rel_type, "out" as direction
                """
            else:
                query = """
                MATCH (n)-[r]->(m)
                WHERE id(n) = $node_id
                RETURN id(m) as node_id, labels(m) as labels, properties(m) as props,
                       type(r) as rel_type, "out" as direction
                """
            rows = self._execute_query(query, {"node_id": node_id})
            for row in rows:
                node = self._row_to_graph_node(row)
                results.append((node, row.get("rel_type", "UNKNOWN"), "out"))

        if direction in ("in", "both"):
            if rel_type:
                query = f"""
                MATCH (n)<-[r:{rel_type}]-(m)
                WHERE id(n) = $node_id
                RETURN id(m) as node_id, labels(m) as labels, properties(m) as props,
                       type(r) as rel_type, "in" as direction
                """
            else:
                query = """
                MATCH (n)<-[r]-(m)
                WHERE id(n) = $node_id
                RETURN id(m) as node_id, labels(m) as labels, properties(m) as props,
                       type(r) as rel_type, "in" as direction
                """
            rows = self._execute_query(query, {"node_id": node_id})
            for row in rows:
                node = self._row_to_graph_node(row)
                results.append((node, row.get("rel_type", "UNKNOWN"), "in"))

        return results

    def _row_to_graph_node(self, row: ResultRow) -> GraphNode:
        """Convert a query result row to GraphNode.

        Args:
            row: Query result row

        Returns:
            GraphNode instance
        """
        props = row.get("props", {})
        if isinstance(props, dict):
            properties = dict(props)
        else:
            properties = {}

        labels = row.get("labels", [])
        if not isinstance(labels, list):
            labels = []

        return GraphNode(
            node_id=row.get("node_id", 0),
            labels=labels,
            qualified_name=properties.get("qualified_name", ""),
            name=properties.get("name", ""),
            path=properties.get("path"),
            start_line=properties.get("start_line"),
            end_line=properties.get("end_line"),
            docstring=properties.get("docstring"),
            properties=properties,
        )
