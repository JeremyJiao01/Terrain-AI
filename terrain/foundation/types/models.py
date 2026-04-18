"""Terrain - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple

from .constants import SupportedLanguage

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass(frozen=True)
class LanguageSpec:
    """Specification for a programming language."""

    language: SupportedLanguage | str
    file_extensions: tuple[str, ...]
    function_node_types: tuple[str, ...]
    class_node_types: tuple[str, ...]
    module_node_types: tuple[str, ...]
    call_node_types: tuple[str, ...] = ()
    import_node_types: tuple[str, ...] = ()
    import_from_node_types: tuple[str, ...] = ()
    name_field: str = "name"
    body_field: str = "body"
    package_indicators: tuple[str, ...] = ()
    function_query: str | None = None
    class_query: str | None = None
    call_query: str | None = None
    typedef_query: str | None = None
    macro_query: str | None = None
    func_ptr_assign_query: str | None = None
    predicate_query: str | None = None


class FQNSpec(NamedTuple):
    """Specification for building fully qualified names."""

    scope_node_types: frozenset[str]
    function_node_types: frozenset[str]
    get_name: Callable[["Node"], str | None]
    file_to_module_parts: Callable[[Path, Path], list[str]]


@dataclass
class Dependency:
    """Represents a project dependency."""

    name: str
    spec: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class GraphNode:
    """Represents a node in the graph."""

    node_id: int
    labels: list[str]
    properties: dict[str, PropertyValue]


@dataclass
class GraphRelationship:
    """Represents a relationship in the graph."""

    from_id: int
    to_id: int
    type: str
    properties: dict[str, PropertyValue]


@dataclass
class FunctionInfo:
    """Information about a function."""

    name: str
    qualified_name: str
    start_line: int
    end_line: int
    docstring: str | None
    decorators: list[str]
    is_method: bool
    parent_class: str | None
    return_type: str | None = None
    parameters: list[str] | None = None
    signature: str | None = None
    visibility: str | None = None


@dataclass
class ClassInfo:
    """Information about a class."""

    name: str
    qualified_name: str
    start_line: int
    end_line: int
    parent_classes: list[str]
    decorators: list[str]


@dataclass
class CallInfo:
    """Information about a function call."""

    caller_qualified_name: str
    callee_name: str
    callee_qualified_name: str | None
    line_number: int


# Type alias for property values
PropertyValue = str | int | float | bool | list[str] | None
