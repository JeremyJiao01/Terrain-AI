"""Terrain - Predicate Processor (slice 1/3 of JER-47).

Extracts predicate nodes (if / while / do-while / for / switch-case / ternary)
from a C function's AST subtree. Slice 1 returns only the predicate skeleton —
``kind`` / ``location`` / ``expression`` / ``nesting_path``. ``symbols_referenced``,
``guarded_block`` and related fields are reserved for later slices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tree_sitter import Node, Query, QueryCursor

from terrain.foundation.types import constants as cs
from .utils import safe_decode_with_fallback

if TYPE_CHECKING:
    pass


# Predicate-class node types used to build nesting_path (outer constructs).
_PREDICATE_ANCESTOR_TYPES = frozenset({
    "if_statement",
    "while_statement",
    "do_statement",
    "for_statement",
    "switch_statement",
    "case_statement",
})


def _strip_outer_parens(text: str) -> str:
    """Strip one pair of balanced outer parentheses + surrounding whitespace."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == "(" and stripped[-1] == ")":
        depth = 0
        for i, ch in enumerate(stripped):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(stripped) - 1:
                    return stripped  # outer parens are not balanced across whole text
        return stripped[1:-1].strip()
    return stripped


def _is_else_if(node: Node) -> bool:
    """Return True when *node* is an if_statement that is the direct
    if_statement child of an ``else_clause`` (the C ``else if`` construct).

    In tree-sitter-c the AST shape is::

        if_statement
          alternative: else_clause
                       else
                       if_statement   ← this is the "else if"
    """
    if node.type != "if_statement":
        return False
    parent = node.parent
    if parent is None or parent.type != "else_clause":
        return False
    for child in parent.named_children:
        if child.type == "if_statement":
            return child == node
    return False


def _condition_text(node: Node) -> str:
    """Extract the condition text of a construct, stripping outer parens."""
    cond = node.child_by_field_name("condition")
    if cond is None:
        return ""
    return _strip_outer_parens(safe_decode_with_fallback(cond))


def _for_header_text(node: Node) -> str:
    """Reconstruct the ``for (init; cond; update)`` header text."""
    init = node.child_by_field_name("initializer")
    cond = node.child_by_field_name("condition")
    update = node.child_by_field_name("update")
    init_s = safe_decode_with_fallback(init).strip() if init is not None else ""
    cond_s = safe_decode_with_fallback(cond).strip() if cond is not None else ""
    update_s = safe_decode_with_fallback(update).strip() if update is not None else ""
    # initializer may already end with ';' when it is a declaration; normalise.
    init_s = init_s.rstrip(";").strip()
    return f"{init_s}; {cond_s}; {update_s}"


def _case_expression(node: Node) -> str:
    """Return ``"case <value>"`` or ``"default"`` for a case_statement."""
    value = node.child_by_field_name("value")
    if value is None:
        return "default"
    return f"case {safe_decode_with_fallback(value).strip()}"


def _classify(node: Node, capture_name: str) -> str:
    """Map a captured node + capture name to a predicate ``kind`` string."""
    if capture_name == cs.CAPTURE_PREDICATE_IF:
        return "else_if" if _is_else_if(node) else "if"
    if capture_name == cs.CAPTURE_PREDICATE_WHILE:
        return "while"
    if capture_name == cs.CAPTURE_PREDICATE_DO_WHILE:
        return "do_while"
    if capture_name == cs.CAPTURE_PREDICATE_FOR:
        return "for"
    if capture_name == cs.CAPTURE_PREDICATE_SWITCH_CASE:
        return "switch_case"
    if capture_name == cs.CAPTURE_PREDICATE_TERNARY:
        return "ternary"
    return capture_name


def _node_expression(node: Node, kind: str) -> str:
    """Compute the ``expression`` field for a predicate node based on kind."""
    if kind in ("if", "else_if", "while", "do_while", "switch"):
        return _condition_text(node)
    if kind == "for":
        cond = node.child_by_field_name("condition")
        if cond is None:
            return ""
        return safe_decode_with_fallback(cond).strip()
    if kind == "switch_case":
        return _case_expression(node)
    if kind == "ternary":
        cond = node.child_by_field_name("condition")
        if cond is None:
            return ""
        return safe_decode_with_fallback(cond).strip()
    return ""


def _ancestor_header(anc: Node) -> str | None:
    """Compute the nesting_path entry for a predicate-class ancestor."""
    t = anc.type
    if t == "if_statement":
        cond = _condition_text(anc)
        prefix = "else if" if _is_else_if(anc) else "if"
        return f"{prefix} ({cond})"
    if t == "while_statement":
        return f"while ({_condition_text(anc)})"
    if t == "do_statement":
        return f"do..while ({_condition_text(anc)})"
    if t == "for_statement":
        return f"for ({_for_header_text(anc)})"
    if t == "switch_statement":
        return f"switch ({_condition_text(anc)})"
    if t == "case_statement":
        return f"{_case_expression(anc)}"
    return None


def _nesting_path(node: Node, stop_node: Node) -> list[str]:
    """Walk upward from *node* (exclusive) up to but not including *stop_node*,
    collecting predicate-class ancestor headers. Returns outermost-first.

    Predicate-class nodes: if / while / do / for / switch / case plus bare
    ``else`` clauses. Block / compound_statement nodes are skipped.

    ``else_clause`` only contributes ``"else"`` when its body is NOT an
    ``else if`` — otherwise the inner if_statement is already emitted as an
    ``else if (...)`` entry and we'd double up.
    """
    path: list[str] = []
    prev = node
    current = node.parent
    while current is not None and current != stop_node:
        header: str | None = None
        if current.type == "else_clause":
            direct_if: Node | None = None
            for c in current.named_children:
                if c.type == "if_statement":
                    direct_if = c
                    break
            if direct_if is None or direct_if != prev:
                header = "else"
        elif current.type in _PREDICATE_ANCESTOR_TYPES:
            header = _ancestor_header(current)
        if header is not None:
            path.append(header)
        prev = current
        current = current.parent
    path.reverse()
    return path


def extract_predicates(
    function_node: Node,
    query: Query,
    rel_file_path: str,
) -> list[dict[str, object]]:
    """Extract all predicates inside *function_node*.

    Returns a flat list of dicts: ``{kind, location, expression, nesting_path}``,
    sorted by (line, column) of the predicate node's start.
    """
    cursor = QueryCursor(query)
    captures = cursor.captures(function_node)

    # Deduplicate by node id — a node may appear under multiple captures in
    # theory, and we want one entry per predicate node with a stable kind.
    seen: dict[int, tuple[Node, str]] = {}
    capture_order = (
        cs.CAPTURE_PREDICATE_IF,
        cs.CAPTURE_PREDICATE_WHILE,
        cs.CAPTURE_PREDICATE_DO_WHILE,
        cs.CAPTURE_PREDICATE_FOR,
        cs.CAPTURE_PREDICATE_SWITCH_CASE,
        cs.CAPTURE_PREDICATE_TERNARY,
    )
    for capture_name in capture_order:
        for node in captures.get(capture_name, []):
            if not isinstance(node, Node):
                continue
            seen.setdefault(node.id, (node, capture_name))

    out: list[dict[str, object]] = []
    for node, capture_name in seen.values():
        kind = _classify(node, capture_name)
        expression = _node_expression(node, kind)
        line = node.start_point[0] + 1
        entry: dict[str, object] = {
            "kind": kind,
            "location": f"{rel_file_path}:{line}",
            "expression": expression,
            "nesting_path": _nesting_path(node, function_node),
        }
        out.append(entry)

    out.sort(key=lambda e: (
        int(str(e["location"]).rsplit(":", 1)[-1]),
        str(e["kind"]),
    ))
    return out
