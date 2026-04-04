"""Code Graph Builder - Type Definitions."""

from __future__ import annotations

from collections.abc import ItemsView, KeysView, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple, Protocol, TypedDict

from .constants import NodeLabel, RelationshipType, SupportedLanguage

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser, Query

    from .models import LanguageSpec


# Basic type aliases
PropertyValue = str | int | float | bool | list[str] | None
PropertyDict = dict[str, PropertyValue]

ResultScalar = str | int | float | bool | None
ResultValue = ResultScalar | list[ResultScalar] | dict[str, ResultScalar]
ResultRow = dict[str, ResultValue]


# Node and relationship types
class NodeType(StrEnum):
    FUNCTION = "Function"
    METHOD = "Method"
    CLASS = "Class"
    MODULE = "Module"
    INTERFACE = "Interface"
    PACKAGE = "Package"
    ENUM = "Enum"
    TYPE = "Type"
    UNION = "Union"


# Type aliases for function registry
SimpleName = str
QualifiedName = str
SimpleNameLookup = dict[SimpleName, set[QualifiedName]]
TrieNode = dict[str, "TrieNode | QualifiedName | NodeType"]
FunctionRegistry = dict[QualifiedName, NodeType]


# AST types (use string literal for forward reference since Node is TYPE_CHECKING only)
ASTNode = "Node"


# Graph data types
class GraphMetadata(TypedDict):
    total_nodes: int
    total_relationships: int
    exported_at: str


class NodeData(TypedDict):
    node_id: int
    labels: list[str]
    properties: dict[str, PropertyValue]


class RelationshipData(TypedDict):
    from_id: int
    to_id: int
    type: str
    properties: dict[str, PropertyValue]


class GraphData(TypedDict):
    nodes: list[NodeData] | list[ResultRow]
    relationships: list[RelationshipData] | list[ResultRow]
    metadata: GraphMetadata


class GraphSummary(TypedDict):
    total_nodes: int
    total_relationships: int
    node_labels: dict[str, int]
    relationship_types: dict[str, int]
    metadata: GraphMetadata


# Batch types
class NodeBatchRow(TypedDict):
    id: PropertyValue
    props: PropertyDict


class RelBatchRow(TypedDict):
    from_val: PropertyValue
    to_val: PropertyValue
    props: PropertyDict


BatchParams = NodeBatchRow | RelBatchRow | PropertyDict


class BatchWrapper(TypedDict):
    batch: Sequence[BatchParams]


# Language query types
class LanguageQueries(TypedDict, total=False):
    functions: Query | None
    classes: Query | None
    calls: Query | None
    imports: Query | None
    locals: Query | None
    typedefs: Query | None
    macros: Query | None
    config: LanguageSpec
    language: Language
    parser: Parser


# Function match types
class FunctionMatch(TypedDict):
    node: Node
    simple_name: str
    qualified_name: str
    parent_class: str | None
    line_number: int


# Embedding query result
class EmbeddingQueryResult(TypedDict):
    node_id: int
    qualified_name: str
    start_line: int | None
    end_line: int | None
    path: str | None


# Build result
@dataclass
class BuildResult:
    """Result of building a code graph."""

    project_name: str
    nodes_created: int
    relationships_created: int
    functions_found: int
    classes_found: int
    files_processed: int
    errors: list[str]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


# Protocols
class FunctionRegistryTrieProtocol(Protocol):
    def __contains__(self, qualified_name: QualifiedName) -> bool: ...
    def __getitem__(self, qualified_name: QualifiedName) -> NodeType: ...
    def __setitem__(self, qualified_name: QualifiedName, func_type: NodeType) -> None: ...
    def get(self, qualified_name: QualifiedName, default: NodeType | None = None) -> NodeType | None: ...
    def keys(self) -> KeysView[QualifiedName]: ...
    def items(self) -> ItemsView[QualifiedName, NodeType]: ...
    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]: ...
    def find_ending_with(self, suffix: str) -> list[QualifiedName]: ...


class ASTCacheProtocol(Protocol):
    def __setitem__(self, key: Path, value: tuple[Node, SupportedLanguage]) -> None: ...
    def __getitem__(self, key: Path) -> tuple[Node, SupportedLanguage]: ...
    def __delitem__(self, key: Path) -> None: ...
    def __contains__(self, key: Path) -> bool: ...
    def items(self) -> ItemsView[Path, tuple[Node, SupportedLanguage]]: ...


class ColumnDescriptor(Protocol):
    @property
    def name(self) -> str: ...


class CursorProtocol(Protocol):
    def execute(
        self,
        query: str,
        params: PropertyDict | Sequence[BatchParams] | BatchWrapper | None = None,
    ) -> None: ...
    def close(self) -> None: ...
    @property
    def description(self) -> Sequence[ColumnDescriptor] | None: ...
    def fetchall(self) -> list[tuple[PropertyValue, ...]]: ...


# Node identifier type
NodeIdentifier = tuple[NodeLabel | str, str, str | None]


# Language import type
class LanguageImport(NamedTuple):
    lang_key: SupportedLanguage
    module_path: str
    attr_name: str
    submodule_name: SupportedLanguage


# Language loader type (use string literal for forward reference since Language is TYPE_CHECKING only)
LanguageLoader = "Callable[[], Language] | None"


# Graph node for search results
@dataclass
class GraphNode:
    """A node in the code graph with full information.

    Attributes:
        node_id: Unique node identifier
        labels: Node labels (e.g., ["Function", "Method"])
        qualified_name: Fully qualified name
        name: Simple name
        path: File path
        start_line: Start line number
        end_line: End line number
        docstring: Documentation string
        properties: Additional properties
    """

    node_id: int
    labels: list[str]
    qualified_name: str
    name: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    docstring: str | None = None
    properties: dict[str, PropertyValue] = field(default_factory=dict)


# Semantic search result with graph node
@dataclass
class SemanticSearchResult:
    """Result from semantic search with graph node information.

    Attributes:
        node: The graph node
        score: Similarity score (0-1)
        embedding: The embedding vector (optional)
    """

    node: GraphNode
    score: float
    embedding: list[float] | None = None


# Graph service protocol
class GraphServiceProtocol(Protocol):
    """Protocol for graph database services."""

    def get_node_by_id(self, node_id: int) -> GraphNode | None:
        """Get a node by its ID.

        Args:
            node_id: Node identifier

        Returns:
            GraphNode if found, None otherwise
        """
        ...

    def get_nodes_by_ids(self, node_ids: list[int]) -> list[GraphNode]:
        """Get multiple nodes by their IDs.

        Args:
            node_ids: List of node identifiers

        Returns:
            List of GraphNode objects
        """
        ...

    def search_nodes(
        self,
        query: str,
        label: str | None = None,
        limit: int = 10,
    ) -> list[GraphNode]:
        """Search nodes by name or qualified name.

        Args:
            query: Search query string
            label: Optional node label filter
            limit: Maximum number of results

        Returns:
            List of matching GraphNode objects
        """
        ...

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
        ...

    def execute_query(
        self,
        query: str,
        params: PropertyDict | None = None,
    ) -> list[ResultRow]:
        """Execute a Cypher query.

        Args:
            query: Cypher query string
            params: Optional query parameters

        Returns:
            List of result rows
        """
        ...

    def close(self) -> None:
        """Close the service connection."""
        ...
