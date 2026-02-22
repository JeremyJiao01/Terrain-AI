"""Code Graph Builder - Main API."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from .config import ConfigValidator, EmbeddingConfig, KuzuConfig, MemgraphConfig, MemoryConfig, ScanConfig
from .constants import SupportedLanguage
from .graph_updater import GraphUpdater
from .parser_loader import load_parsers
from .services.graph_service import MemgraphIngestor
from .services.kuzu_service import KuzuIngestor
from .types import BuildResult, GraphData, GraphSummary, PropertyDict, ResultRow

if TYPE_CHECKING:
    from tree_sitter import Parser

    from .types import LanguageQueries


class CodeGraphBuilder:
    """Main API for building code knowledge graphs.

    Supports multiple backends:
    - Memgraph: Full-featured graph database (requires Docker)
    - Kùzu: Embedded graph database (no Docker, recommended for local use)
    - Memory: In-memory storage only (no persistence)

    Examples:
        >>> # Method 1: Simple dict-based config (recommended for quick start)
        >>> builder = CodeGraphBuilder(
        ...     repo_path="/path/to/repo",
        ...     backend="kuzu",
        ...     backend_config={"db_path": "./graph.db"}
        ... )
        >>> result = builder.build_graph()

        >>> # Method 2: Using config dataclasses (type-safe)
        >>> from code_graph_builder.config import KuzuConfig, ScanConfig
        >>> builder = CodeGraphBuilder(
        ...     repo_path="/path/to/repo",
        ...     backend="kuzu",
        ...     backend_config=KuzuConfig(db_path="./graph.db"),
        ...     scan_config=ScanConfig(exclude_patterns={"tests", "docs"})
        ... )

        >>> # Method 3: Memgraph backend
        >>> builder = CodeGraphBuilder(
        ...     repo_path="/path/to/repo",
        ...     backend="memgraph",
        ...     backend_config={"host": "localhost", "port": 7687}
        ... )
    """

    def __init__(
        self,
        repo_path: str | Path,
        backend: str = "kuzu",
        backend_config: dict[str, Any] | KuzuConfig | MemgraphConfig | MemoryConfig | None = None,
        scan_config: dict[str, Any] | ScanConfig | None = None,
        embedding_config: dict[str, Any] | EmbeddingConfig | None = None,
        # Backward compatibility
        db_config: dict | None = None,
        exclude_paths: frozenset[str] | None = None,
        unignore_paths: frozenset[str] | None = None,
    ) -> None:
        """Initialize the code graph builder.

        Args:
            repo_path: Path to the repository to analyze
            backend: Database backend to use ("kuzu", "memgraph", or "memory")
            backend_config: Backend configuration. Can be:
                - dict: {"db_path": "...", "batch_size": 1000}
                - KuzuConfig: Type-safe configuration for Kùzu
                - MemgraphConfig: Type-safe configuration for Memgraph
                - MemoryConfig: Type-safe configuration for Memory
            scan_config: Scan configuration. Can be:
                - dict: {"exclude_patterns": {"tests"}, "max_file_size": 1000000}
                - ScanConfig: Type-safe scan configuration
            # Deprecated (use backend_config and scan_config instead):
            db_config: Deprecated, use backend_config
            exclude_paths: Deprecated, use scan_config
            unignore_paths: Deprecated, use scan_config
        """
        self.repo_path = Path(repo_path).resolve()
        self.backend = backend.lower()

        # Handle backward compatibility
        if db_config is not None:
            logger.warning("db_config is deprecated, use backend_config instead")
            if backend_config is None:
                backend_config = db_config

        # Validate and normalize backend config
        self.backend_config = ConfigValidator.validate_backend_config(self.backend, backend_config)

        # Validate and normalize scan config
        if scan_config is None:
            scan_config = ScanConfig()
        elif isinstance(scan_config, dict):
            scan_config = ScanConfig(**scan_config)

        # Handle backward compatibility for exclude_paths/unignore_paths
        if exclude_paths is not None:
            logger.warning("exclude_paths is deprecated, use scan_config instead")
            scan_config.exclude_patterns.update(exclude_paths)
        if unignore_paths is not None:
            logger.warning("unignore_paths is deprecated, use scan_config instead")
            scan_config.unignore_paths.update(unignore_paths)

        self.scan_config = scan_config

        # Validate and normalize embedding config
        if embedding_config is None:
            embedding_config = EmbeddingConfig(enabled=False)
        elif isinstance(embedding_config, dict):
            embedding_config = EmbeddingConfig(**embedding_config)
        self.embedding_config = embedding_config

        self._parsers: dict[SupportedLanguage, Parser] | None = None
        self._queries: dict[SupportedLanguage, LanguageQueries] | None = None
        self._ingestor: MemgraphIngestor | KuzuIngestor | None = None
        self._embedder: Any | None = None
        self._vector_store: Any | None = None

    def _load_parsers(self) -> None:
        """Load Tree-sitter parsers for supported languages."""
        if self._parsers is None or self._queries is None:
            self._parsers, self._queries = load_parsers()
            logger.info(f"Loaded parsers for {len(self._parsers)} languages")

    def _get_ingestor(self) -> MemgraphIngestor | KuzuIngestor | Any:
        """Get or create the graph ingestor based on backend."""
        if self._ingestor is None:
            if self.backend == "memgraph":
                host = self.backend_config.get("host", "localhost")
                port = self.backend_config.get("port", 7687)
                batch_size = self.backend_config.get("batch_size", 1000)
                self._ingestor = MemgraphIngestor(host, port, batch_size)
            elif self.backend == "kuzu":
                db_path = self.backend_config.get("db_path", f"./{self.repo_path.name}_graph.db")
                batch_size = self.backend_config.get("batch_size", 1000)
                self._ingestor = KuzuIngestor(db_path, batch_size)
            elif self.backend == "memory":
                from .services.memory_service import MemoryIngestor

                self._ingestor = MemoryIngestor()
            else:
                raise ValueError(f"Unknown backend: {self.backend}. Use 'memgraph', 'kuzu', or 'memory'")
        return self._ingestor

    def _get_embedder_and_store(self) -> tuple[Any | None, Any | None]:
        """Get or create the embedder and vector store if embedding is enabled."""
        if not self.embedding_config.enabled:
            return None, None

        if self._embedder is None or self._vector_store is None:
            from .embeddings.qwen3_embedder import create_embedder
            from .embeddings.vector_store import create_vector_store

            # Create embedder
            self._embedder = create_embedder(
                model_name=self.embedding_config.model_name,
                device=self.embedding_config.device,
            )

            # Get embedding dimension
            dimension = self.embedding_config.vector_dimension
            if dimension is None:
                dimension = self._embedder.get_embedding_dimension()

            # Create vector store
            self._vector_store = create_vector_store(
                backend=self.embedding_config.vector_store_backend,
                dimension=dimension,
                db_path=self.embedding_config.vector_store_path,
            )

        return self._embedder, self._vector_store

    def build_graph(self, clean: bool = False) -> BuildResult:
        """Build the code knowledge graph.

        Args:
            clean: If True, clean the database before building

        Returns:
            BuildResult with statistics about the build
        """
        self._load_parsers()
        ingestor = self._get_ingestor()

        with ingestor:
            if clean:
                ingestor.clean_database()

            # Get embedder and vector store if embedding is enabled
            embedder, vector_store = self._get_embedder_and_store()

            updater = GraphUpdater(
                ingestor=ingestor,
                repo_path=self.repo_path,
                parsers=self._parsers,
                queries=self._queries,
                unignore_paths=frozenset(self.scan_config.unignore_paths),
                exclude_paths=frozenset(self.scan_config.exclude_patterns),
                embedder=embedder,
                vector_store=vector_store,
                embedding_config=self.embedding_config.to_dict(),
            )

            updater.run()

            # Get statistics
            if hasattr(ingestor, 'get_statistics'):
                stats = ingestor.get_statistics()
                total_nodes = stats.get("node_count", 0)
                total_rels = stats.get("relationship_count", 0)
            else:
                # Fallback for ingestors without get_statistics
                total_nodes = 0
                total_rels = 0

            return BuildResult(
                project_name=self.repo_path.name,
                nodes_created=total_nodes,
                relationships_created=total_rels,
                functions_found=0,  # Will be updated from stats
                classes_found=0,
                files_processed=0,
                errors=[],
            )

    def export_graph(self) -> GraphData:
        """Export the graph data as a dictionary.

        Returns:
            GraphData containing nodes, relationships, and metadata
        """
        ingestor = self._get_ingestor()
        with ingestor:
            if hasattr(ingestor, 'export_graph'):
                return ingestor.export_graph()
            elif hasattr(ingestor, 'export_graph_to_dict'):
                return ingestor.export_graph_to_dict()
            else:
                return {"nodes": [], "relationships": [], "metadata": {}}

    def get_statistics(self) -> dict:
        """Get statistics about the graph.

        Returns:
            Dictionary with node and relationship counts
        """
        ingestor = self._get_ingestor()
        with ingestor:
            if hasattr(ingestor, 'get_statistics'):
                stats = ingestor.get_statistics()
                if isinstance(stats, dict):
                    return {
                        "total_nodes": stats.get("node_count", 0),
                        "total_relationships": stats.get("relationship_count", 0),
                        "node_labels": stats.get("node_labels", {}),
                        "relationship_types": stats.get("relationship_types", {}),
                    }

            # Fallback to export
            if hasattr(ingestor, 'export_graph_to_dict'):
                data = ingestor.export_graph_to_dict()
            elif hasattr(ingestor, 'export_graph'):
                data = ingestor.export_graph()
            else:
                data = {"nodes": [], "relationships": []}

            # Count node labels
            node_labels: dict[str, int] = {}
            for node in data.get("nodes", []):
                labels = node.get("labels", [])
                for label in labels:
                    node_labels[label] = node_labels.get(label, 0) + 1

            # Count relationship types
            rel_types: dict[str, int] = {}
            for rel in data.get("relationships", []):
                rel_type = rel.get("type", "UNKNOWN")
                rel_types[rel_type] = rel_types.get(rel_type, 0) + 1

            return {
                "total_nodes": len(data.get("nodes", [])),
                "total_relationships": len(data.get("relationships", [])),
                "node_labels": node_labels,
                "relationship_types": rel_types,
                "metadata": data.get("metadata", {}),
            }

    def query(self, cypher_query: str, params: PropertyDict | None = None) -> list[ResultRow]:
        """Execute a Cypher query against the graph.

        Args:
            cypher_query: The Cypher query to execute
            params: Optional query parameters

        Returns:
            List of result rows as dictionaries
        """
        ingestor = self._get_ingestor()
        with ingestor:
            if hasattr(ingestor, 'query'):
                return ingestor.query(cypher_query, params)
            elif hasattr(ingestor, 'fetch_all'):
                return ingestor.fetch_all(cypher_query, params)
            else:
                return []

    def get_function_source(self, qualified_name: str) -> str | None:
        """Get the source code of a function by its qualified name.

        Args:
            qualified_name: The fully qualified name of the function

        Returns:
            The source code as a string, or None if not found
        """
        results = self.query(
            """
            MATCH (n)
            WHERE n.qualified_name = $qn
            RETURN n.name AS name, n.start_line AS start, n.end_line AS end
            LIMIT 1
            """,
            {"qn": qualified_name},
        )

        if not results:
            return None

        result = results[0]
        return f"Function: {result.get('name')} (lines {result.get('start')}-{result.get('end')})"

    def list_projects(self) -> list[str]:
        """List all projects in the database.

        Returns:
            List of project names
        """
        ingestor = self._get_ingestor()
        with ingestor:
            if hasattr(ingestor, 'list_projects'):
                return ingestor.list_projects()
            return []

    def delete_project(self, project_name: str | None = None) -> None:
        """Delete a project from the database.

        Args:
            project_name: Name of the project to delete (default: current project)
        """
        name = project_name or self.repo_path.name
        ingestor = self._get_ingestor()
        with ingestor:
            if hasattr(ingestor, 'delete_project'):
                ingestor.delete_project(name)
