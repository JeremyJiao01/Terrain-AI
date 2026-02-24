"""Kùzu embedded graph database service - No Docker required."""

from __future__ import annotations

import json
import types
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from ..types import (
    GraphData,
    PropertyDict,
    PropertyValue,
    ResultRow,
)

if TYPE_CHECKING:
    import kuzu


class KuzuIngestor:
    """Ingestor for writing code graph data to Kùzu embedded database.

    Kùzu is an embedded graph database that requires no server or Docker.
    Perfect for local development and testing.

    Example:
        >>> ingestor = KuzuIngestor("./my_graph.db")
        >>> with ingestor:
        ...     ingestor.ensure_node_batch("Function", {"name": "foo", "id": "1"})
        ...     ingestor.flush_all()
        >>> # Query later
        >>> results = ingestor.query("MATCH (f:Function) RETURN f.name")
    """

    def __init__(self, db_path: str | Path, batch_size: int = 1000):
        """Initialize Kùzu ingestor.

        Args:
            db_path: Path to store the database files
            batch_size: Batch size for writes
        """
        self.db_path = Path(db_path)
        self.batch_size = batch_size
        self._db: kuzu.Database | None = None
        self._conn: kuzu.Connection | None = None
        self.node_buffer: list[tuple[str, dict[str, PropertyValue]]] = []
        self.relationship_buffer: list[
            tuple[
                tuple[str, str, PropertyValue],
                str,
                tuple[str, str, PropertyValue],
                dict[str, PropertyValue] | None,
            ]
        ] = []
        self._initialized = False

    def __enter__(self) -> KuzuIngestor:
        """Enter context manager and initialize database."""
        import kuzu

        logger.info(f"Opening Kùzu database at {self.db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)
        logger.info("Kùzu database opened successfully")
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit context manager and cleanup."""
        if exc_type:
            logger.exception(f"Exception during ingest: {exc_val}")
            try:
                self.flush_all()
            except Exception as flush_err:
                logger.error(f"Flush error during exception handling: {flush_err}")
        else:
            self.flush_all()

        if self._conn:
            # Kùzu connection doesn't need explicit close
            self._conn = None
        if self._db:
            # Kùzu database doesn't need explicit close
            self._db = None
        logger.info("Kùzu database closed")

    def _ensure_schema(self, label: str) -> None:
        """Ensure node table exists for the given label."""
        if not self._conn:
            raise ConnectionError("Not connected to database")

        # Kùzu requires predefined schema, create generic node table
        try:
            # Check if table exists by trying to query
            self._conn.execute(f"MATCH (n:{label}) RETURN n LIMIT 1")
        except Exception:
            # Table doesn't exist, create it
            # Kùzu uses CREATE NODE TABLE with specific properties
            logger.info(f"Creating node table for label: {label}")
            try:
                self._conn.execute(f"""
                    CREATE NODE TABLE {label} (
                        qualified_name STRING,
                        name STRING,
                        path STRING,
                        start_line INT64,
                        end_line INT64,
                        docstring STRING,
                        return_type STRING,
                        signature STRING,
                        visibility STRING,
                        parameters STRING[],
                        PRIMARY KEY (qualified_name)
                    )
                """)
            except Exception as e:
                logger.debug(f"Table creation may have failed (could already exist): {e}")

    def _ensure_rel_schema(self, rel_type: str, from_label: str, to_label: str) -> None:
        """Ensure relationship table exists."""
        if not self._conn:
            raise ConnectionError("Not connected to database")

        try:
            self._conn.execute(f"MATCH ()-[r:{rel_type}]->() RETURN r LIMIT 1")
        except Exception:
            logger.info(f"Creating relationship table: {rel_type}")
            try:
                self._conn.execute(f"""
                    CREATE REL TABLE {rel_type} (
                        FROM {from_label} TO {to_label},
                        MANY_MANY
                    )
                """)
            except Exception as e:
                logger.debug(f"Rel table creation may have failed: {e}")

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        """Add a node to the batch buffer.

        Args:
            label: Node label (e.g., "Function", "Class")
            properties: Node properties dictionary
        """
        self.node_buffer.append((label, properties.copy()))
        if len(self.node_buffer) >= self.batch_size:
            self.flush_nodes()

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, PropertyValue],
        rel_type: str,
        target: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None:
        """Add a relationship to the batch buffer.

        Args:
            source: (label, key, value) tuple for source node
            rel_type: Relationship type (e.g., "CALLS", "DEFINES")
            target: (label, key, value) tuple for target node
            properties: Optional relationship properties
        """
        self.relationship_buffer.append((source, rel_type, target, properties))
        if len(self.relationship_buffer) >= self.batch_size:
            self.flush_relationships()

    def _value_to_cypher(self, value: PropertyValue) -> str:
        """Convert Python value to Cypher literal."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            # Escape quotes
            escaped = value.replace("'", "\\'")
            return f"'{escaped}'"
        if isinstance(value, list):
            items = [self._value_to_cypher(v) for v in value]
            return f"[{', '.join(items)}]"
        return f"'{str(value)}'"

    def flush_nodes(self) -> None:
        """Flush node buffer to database."""
        if not self.node_buffer or not self._conn:
            return

        # Group nodes by label
        by_label: dict[str, list[PropertyDict]] = {}
        for label, props in self.node_buffer:
            if label not in by_label:
                by_label[label] = []
            by_label[label].append(props)

        for label, nodes in by_label.items():
            self._ensure_schema(label)

            for props in nodes:
                # Build CREATE statement
                qualified_name = props.get("qualified_name", props.get("name", ""))
                name = props.get("name", "")
                path = props.get("path", "")
                start_line = props.get("start_line", 0)
                end_line = props.get("end_line", 0)
                docstring = props.get("docstring", "")
                return_type = props.get("return_type", "")
                signature = props.get("signature", "")
                visibility = props.get("visibility", "")
                parameters = props.get("parameters")

                try:
                    cypher = f"""
                        CREATE (n:{label} {{
                            qualified_name: {self._value_to_cypher(qualified_name)},
                            name: {self._value_to_cypher(name)},
                            path: {self._value_to_cypher(path)},
                            start_line: {start_line},
                            end_line: {end_line},
                            docstring: {self._value_to_cypher(docstring)},
                            return_type: {self._value_to_cypher(return_type)},
                            signature: {self._value_to_cypher(signature)},
                            visibility: {self._value_to_cypher(visibility)},
                            parameters: {self._value_to_cypher(parameters if parameters else [])}
                        }})
                    """
                    self._conn.execute(cypher)
                except Exception as e:
                    logger.debug(f"Error creating node: {e}")

        logger.debug(f"Flushed {len(self.node_buffer)} nodes")
        self.node_buffer = []

    def flush_relationships(self) -> None:
        """Flush relationship buffer to database."""
        if not self.relationship_buffer or not self._conn:
            return

        for source, rel_type, target, _props in self.relationship_buffer:
            from_label, from_key, from_val = source
            to_label, to_key, to_val = target

            self._ensure_rel_schema(rel_type, from_label, to_label)

            try:
                cypher = f"""
                    MATCH (a:{from_label} {{{from_key}: {self._value_to_cypher(from_val)}}}),
                          (b:{to_label} {{{to_key}: {self._value_to_cypher(to_val)}}})
                    CREATE (a)-[:{rel_type}]->(b)
                """
                self._conn.execute(cypher)
            except Exception as e:
                logger.debug(f"Error creating relationship: {e}")

        logger.debug(f"Flushed {len(self.relationship_buffer)} relationships")
        self.relationship_buffer = []

    def flush_all(self) -> None:
        """Flush all pending data."""
        self.flush_nodes()
        self.flush_relationships()

    def query(self, cypher_query: str, params: PropertyDict | None = None) -> list[ResultRow]:
        """Execute a Cypher query.

        Args:
            cypher_query: Cypher query string
            params: Optional query parameters

        Returns:
            List of result rows as dictionaries
        """
        if not self._conn:
            raise ConnectionError("Not connected to database")

        try:
            result = self._conn.execute(cypher_query)
            # Convert Kùzu result to list of dicts
            rows = []
            while result.has_next():
                row = result.get_next()
                # Convert to dict (column names are not easily available in Kùzu)
                rows.append({"result": row})
            return rows
        except Exception as e:
            logger.error(f"Query error: {e}")
            return []

    def fetch_module_apis(
        self,
        module_qn: str | None = None,
        visibility: str | None = "public",
    ) -> list[ResultRow]:
        """Fetch API interfaces (functions) for a module or the entire project.

        Args:
            module_qn: Qualified name of a module. If None, returns APIs across all modules.
            visibility: Filter by visibility ("public", "static", or None for all).

        Returns:
            List of result rows with function name, signature, return_type, etc.
        """
        if not self._conn:
            raise ConnectionError("Not connected to database")

        conditions: list[str] = []
        if module_qn:
            safe_qn = module_qn.replace("'", "\\'")
            conditions.append(f"m.qualified_name = '{safe_qn}'")
        if visibility:
            conditions.append(f"f.visibility = '{visibility}'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cypher = f"""
            MATCH (m:Module)-[:DEFINES]->(f:Function)
            {where_clause}
            RETURN m.qualified_name AS module,
                   f.name AS name,
                   f.signature AS signature,
                   f.return_type AS return_type,
                   f.visibility AS visibility,
                   f.parameters AS parameters,
                   f.start_line AS start_line,
                   f.end_line AS end_line
            ORDER BY m.qualified_name, f.start_line
        """

        try:
            return self.query(cypher)
        except Exception as e:
            logger.error(f"fetch_module_apis error: {e}")
            return []

    def clean_database(self) -> None:
        """Clean all data from the database."""
        if not self._conn:
            raise ConnectionError("Not connected to database")

        try:
            # Drop all tables
            result = self._conn.execute("CALL show_tables() RETURN *")
            tables = []
            while result.has_next():
                row = result.get_next()
                tables.append(row[0] if row else None)

            for table in tables:
                if table:
                    try:
                        # Quote table name to handle special cases (e.g., numeric names)
                        self._conn.execute(f'DROP TABLE "{table}"')
                    except Exception as e:
                        logger.debug(f"Error dropping table {table}: {e}")

            logger.info("Database cleaned")
        except Exception as e:
            logger.error(f"Error cleaning database: {e}")

    def export_graph(self) -> GraphData:
        """Export the entire graph as GraphData."""
        if not self._conn:
            raise ConnectionError("Not connected to database")

        nodes = []
        relationships = []

        try:
            # Get all nodes
            result = self._conn.execute("MATCH (n) RETURN n")
            while result.has_next():
                row = result.get_next()
                if row and len(row) > 0:
                    node = row[0]
                    nodes.append({
                        "label": node.get("_label", "Unknown"),
                        "properties": dict(node),
                    })

            # Get all relationships
            result = self._conn.execute("MATCH (a)-[r]->(b) RETURN a, r, b")
            while result.has_next():
                row = result.get_next()
                if row and len(row) >= 3:
                    relationships.append({
                        "source": {"qualified_name": row[0].get("qualified_name", "")},
                        "type": row[1].get("_label", "UNKNOWN"),
                        "target": {"qualified_name": row[2].get("qualified_name", "")},
                    })

        except Exception as e:
            logger.error(f"Export error: {e}")

        return {"nodes": nodes, "relationships": relationships}

    def get_statistics(self) -> dict[str, Any]:
        """Get database statistics."""
        if not self._conn:
            raise ConnectionError("Not connected to database")

        stats: dict[str, Any] = {
            "node_count": 0,
            "relationship_count": 0,
            "node_labels": {},
            "relationship_types": {},
        }

        try:
            # Count nodes
            result = self._conn.execute("MATCH (n) RETURN count(n) as count")
            if result.has_next():
                stats["node_count"] = result.get_next()[0]

            # Count relationships
            result = self._conn.execute("MATCH ()-[r]->() RETURN count(r) as count")
            if result.has_next():
                stats["relationship_count"] = result.get_next()[0]

            # Get labels with counts
            result = self._conn.execute("CALL show_tables() RETURN *")
            while result.has_next():
                row = result.get_next()
                if row:
                    label = row[0]
                    # Count nodes for this label
                    try:
                        count_result = self._conn.execute(f"MATCH (n:{label}) RETURN count(n) as count")
                        if count_result.has_next():
                            count = count_result.get_next()[0]
                            stats["node_labels"][label] = count
                    except Exception:
                        stats["node_labels"][label] = 0

        except Exception as e:
            logger.error(f"Statistics error: {e}")

        return stats
