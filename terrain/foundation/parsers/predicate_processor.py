"""Terrain - Predicate Processor (slice 1-2/3 of JER-47).

Extracts predicate nodes (if / while / do-while / for / switch-case / ternary)
from a C function's AST subtree. Slice 1 returns the predicate skeleton —
``kind`` / ``location`` / ``expression`` / ``nesting_path``. Slice 2 adds
``symbols_referenced`` and ``guarded_block.{start_line, end_line, contains_calls}``.
``contains_assignments`` and ``has_early_return`` are reserved for slice 3.
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


_SYMBOL_NODE_TYPES = frozenset({
    # Plain value identifiers — tree-sitter-c parses field / type names as
    # separate node types (field_identifier / type_identifier) so those are
    # naturally excluded here.
    "identifier",
    # Named constants: tree-sitter-c recognises NULL / TRUE / FALSE / true /
    # false as distinct node types. They are still "symbols" for downstream
    # classification (macros in practice), so we keep them.
    "null",
    "true",
    "false",
})


def _collect_identifiers(node: Node | None, out: list[str], seen: set[str]) -> None:
    """Walk *node*'s subtree in source order and append symbol tokens to *out*,
    preserving first-occurrence order and skipping duplicates.

    Only nodes whose type is in :data:`_SYMBOL_NODE_TYPES` are collected. String
    literals, number literals, type identifiers and field identifiers are all
    left out by construction.
    """
    if node is None:
        return
    if node.type in _SYMBOL_NODE_TYPES:
        text = safe_decode_with_fallback(node).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        return
    for child in node.children:
        _collect_identifiers(child, out, seen)


def _symbols_referenced(node: Node, kind: str) -> list[str]:
    """Identifiers referenced in the predicate's condition, source order +
    first-occurrence dedup. For ``for`` loops all three header parts contribute.
    """
    out: list[str] = []
    seen: set[str] = set()
    if kind == "for":
        for field_name in ("initializer", "condition", "update"):
            _collect_identifiers(node.child_by_field_name(field_name), out, seen)
    elif kind == "switch_case":
        _collect_identifiers(node.child_by_field_name("value"), out, seen)
    else:
        _collect_identifiers(node.child_by_field_name("condition"), out, seen)
    return out


def _call_name(call_node: Node) -> str | None:
    """Extract the simple function name from a ``call_expression``.

    Handles ordinary ``foo(x)`` (identifier), field calls ``obj->cb(x)`` /
    ``obj.cb(x)`` (field_expression -> field name), and parenthesised pointer
    dereferences ``(*cb)(x)``. Falls back to the raw text for other shapes.
    """
    func = call_node.child_by_field_name("function")
    if func is None:
        return None
    if func.type == "identifier":
        text = safe_decode_with_fallback(func).strip()
        return text or None
    if func.type == "field_expression":
        field = func.child_by_field_name("field")
        if field is not None:
            text = safe_decode_with_fallback(field).strip()
            if text:
                return text
    if func.type == "parenthesized_expression":
        for child in func.named_children:
            if child.type == "pointer_expression":
                arg = child.child_by_field_name("argument")
                if arg is not None and arg.type == "identifier":
                    text = safe_decode_with_fallback(arg).strip()
                    if text:
                        return text
            elif child.type == "identifier":
                text = safe_decode_with_fallback(child).strip()
                if text:
                    return text
    fallback = safe_decode_with_fallback(func).strip()
    return fallback or None


def _switch_case_body(case_node: Node) -> tuple[Node | None, Node | None]:
    """Return (first_stmt, last_stmt) for the body of a ``case_statement`` —
    i.e. the named children excluding the ``value`` field.

    Either may be None when the case is empty (pure fallthrough).
    """
    value_node = case_node.child_by_field_name("value")
    body: list[Node] = []
    for child in case_node.named_children:
        if value_node is not None and child == value_node:
            continue
        body.append(child)
    if not body:
        return None, None
    return body[0], body[-1]


def _guarded_block(
    node: Node,
    kind: str,
    call_query: Query | None,
) -> dict[str, object] | None:
    """Build the ``guarded_block`` dict for a predicate node.

    Returns ``{start_line, end_line, contains_calls}``. For kinds without a
    meaningful body (none currently) returns None.
    """
    body: Node | None = None
    search_nodes: list[Node] = []
    if kind in ("if", "else_if"):
        body = node.child_by_field_name("consequence")
        if body is not None:
            search_nodes = [body]
    elif kind in ("while", "do_while", "for"):
        body = node.child_by_field_name("body")
        if body is not None:
            search_nodes = [body]
    elif kind == "ternary":
        body = node.child_by_field_name("consequence")
        if body is not None:
            search_nodes = [body]
    elif kind == "switch_case":
        first, last = _switch_case_body(node)
        if first is None or last is None:
            # Empty case (fallthrough) — degenerate block with no body.
            fallback_line = node.end_point[0] + 1
            return {
                "start_line": fallback_line,
                "end_line": fallback_line,
                "contains_calls": [],
            }
        # Collect every named sibling statement so the call search covers the
        # whole case, not just the first statement.
        value_node = node.child_by_field_name("value")
        for child in node.named_children:
            if value_node is not None and child == value_node:
                continue
            search_nodes.append(child)
        start_line = first.start_point[0] + 1
        end_line = last.end_point[0] + 1
        return {
            "start_line": start_line,
            "end_line": end_line,
            "contains_calls": _contains_calls(search_nodes, call_query),
        }

    if body is None:
        return None

    return {
        "start_line": body.start_point[0] + 1,
        "end_line": body.end_point[0] + 1,
        "contains_calls": _contains_calls(search_nodes, call_query),
    }


def _contains_calls(roots: list[Node], call_query: Query | None) -> list[str]:
    """Run ``call_query`` on each root subtree, returning function names in
    source order with first-occurrence dedup. If *call_query* is None, returns
    an empty list.
    """
    if call_query is None or not roots:
        return []
    found: list[tuple[int, str]] = []  # (start_byte, name)
    for root in roots:
        cursor = QueryCursor(call_query)
        captures = cursor.captures(root)
        for cap_name in ("call",):
            for call_node in captures.get(cap_name, []):
                if not isinstance(call_node, Node):
                    continue
                name = _call_name(call_node)
                if not name:
                    continue
                found.append((call_node.start_byte, name))
    found.sort(key=lambda e: e[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, name in found:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


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
    call_query: Query | None = None,
) -> list[dict[str, object]]:
    """Extract all predicates inside *function_node*.

    Returns a flat list of dicts containing
    ``{kind, location, expression, nesting_path, symbols_referenced, guarded_block}``,
    sorted by (line, kind) of the predicate node's start. When *call_query* is
    omitted the ``guarded_block.contains_calls`` list is empty.
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
            "symbols_referenced": _symbols_referenced(node, kind),
        }
        block = _guarded_block(node, kind, call_query)
        if block is not None:
            entry["guarded_block"] = block
        out.append(entry)

    out.sort(key=lambda e: (
        int(str(e["location"]).rsplit(":", 1)[-1]),
        str(e["kind"]),
    ))
    return out
