"""Code Graph Builder - Parser Utilities."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger
from tree_sitter import Node, Query, QueryCursor

from .. import constants as cs
from ..types import ASTNode, LanguageQueries, NodeType, PropertyDict, SimpleNameLookup

if TYPE_CHECKING:
    from ..language_spec import LanguageSpec
    from ..services import IngestorProtocol
    from ..types import FunctionRegistryTrieProtocol


class FunctionCapturesResult(NamedTuple):
    """Result of capturing functions from AST."""

    lang_config: LanguageSpec
    captures: dict[str, list[ASTNode]]


def get_function_captures(
    root_node: ASTNode,
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> FunctionCapturesResult | None:
    """Get function captures from AST using Tree-sitter query."""
    lang_queries = queries[language]
    lang_config = lang_queries[cs.QUERY_CONFIG]

    if not (query := lang_queries[cs.QUERY_FUNCTIONS]):
        return None

    cursor = QueryCursor(query)
    captures = cursor.captures(root_node)
    return FunctionCapturesResult(lang_config, captures)


@lru_cache(maxsize=10000)
def _cached_decode_bytes(text_bytes: bytes) -> str:
    """Cached byte decoding for performance.

    After :func:`normalize_to_utf8_bytes` pre-processing, input should
    always be valid UTF-8.  ``errors="replace"`` is a safety net.
    """
    return text_bytes.decode(cs.ENCODING_UTF8, errors="replace")


def safe_decode_text(node: ASTNode | None) -> str | None:
    """Safely decode node text to string."""
    if node is None or (text_bytes := node.text) is None:
        return None
    if isinstance(text_bytes, bytes):
        return _cached_decode_bytes(text_bytes)
    return str(text_bytes)


def safe_decode_with_fallback(node: ASTNode | None, fallback: str = "") -> str:
    """Safely decode node text with fallback."""
    return result if (result := safe_decode_text(node)) is not None else fallback


def contains_node(parent: ASTNode, target: ASTNode) -> bool:
    """Check if parent contains target node."""
    return parent == target or any(
        contains_node(child, target) for child in parent.children
    )


def ingest_method(
    method_node: ASTNode,
    container_qn: str,
    container_type: cs.NodeLabel,
    ingestor: IngestorProtocol,
    function_registry: FunctionRegistryTrieProtocol,
    simple_name_lookup: SimpleNameLookup,
    get_docstring_func: Callable[[ASTNode], str | None],
    language: cs.SupportedLanguage | None = None,
    extract_decorators_func: Callable[[ASTNode], list[str]] | None = None,
    method_qualified_name: str | None = None,
) -> None:
    """Ingest a method node into the graph."""
    # Extract method name
    if language == cs.SupportedLanguage.CPP:
        from .cpp import utils as cpp_utils

        method_name = cpp_utils.extract_function_name(method_node)
        if not method_name:
            return
    elif not (method_name_node := method_node.child_by_field_name(cs.FIELD_NAME)):
        return
    elif (text := method_name_node.text) is None:
        return
    else:
        method_name = text.decode(cs.ENCODING_UTF8)

    method_qn = method_qualified_name or f"{container_qn}.{method_name}"

    decorators = extract_decorators_func(method_node) if extract_decorators_func else []

    method_props: PropertyDict = {
        cs.KEY_QUALIFIED_NAME: method_qn,
        cs.KEY_NAME: method_name,
        cs.KEY_DECORATORS: decorators,
        cs.KEY_START_LINE: method_node.start_point[0] + 1,
        cs.KEY_END_LINE: method_node.end_point[0] + 1,
        cs.KEY_DOCSTRING: get_docstring_func(method_node),
    }

    logger.info(f"    Found Method: {method_name} (qn: {method_qn})")
    ingestor.ensure_node_batch(cs.NodeLabel.METHOD, method_props)
    function_registry[method_qn] = NodeType.METHOD
    simple_name_lookup[method_name].add(method_qn)

    ingestor.ensure_relationship_batch(
        (container_type, cs.KEY_QUALIFIED_NAME, container_qn),
        cs.RelationshipType.DEFINES_METHOD,
        (cs.NodeLabel.METHOD, cs.KEY_QUALIFIED_NAME, method_qn),
    )


def is_method_node(func_node: ASTNode, lang_config: LanguageSpec) -> bool:
    """Check if a function node is actually a method."""
    current = func_node.parent
    if not isinstance(current, Node):
        return False

    while current and current.type not in lang_config.module_node_types:
        if current.type in lang_config.class_node_types:
            return True
        current = current.parent
    return False
