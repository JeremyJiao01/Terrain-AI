"""Code Graph Builder - Services."""

from typing import Protocol, runtime_checkable

from code_graph_builder.foundation.types.types import PropertyDict, PropertyValue, ResultRow


@runtime_checkable
class IngestorProtocol(Protocol):
    """Protocol for graph data ingestors."""

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None: ...

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None: ...

    def flush_all(self) -> None: ...


@runtime_checkable
class QueryProtocol(Protocol):
    """Protocol for graph query operations."""

    def fetch_all(
        self, query: str, params: PropertyDict | None = None
    ) -> list[ResultRow]: ...

    def execute_write(self, query: str, params: PropertyDict | None = None) -> None: ...


# Import implementation
from .graph_service import MemgraphIngestor

__all__ = ["IngestorProtocol", "QueryProtocol", "MemgraphIngestor"]
