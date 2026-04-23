"""MCP tool registry and handler implementations for Terrain.

Architecture: workspace-based, dynamic service loading.

Workspace layout:
    {TERRAIN_WORKSPACE}/               default: ~/.terrain/
        active.txt                 name of the currently active artifact dir
        {repo_name}_{hash8}/
            meta.json              {repo_path, indexed_at, wiki_page_count}
            graph.db               KùzuDB database
            vectors.pkl            embedding cache
            {repo_name}_structure.pkl  wiki structure cache
            wiki/
                index.md
                wiki/
                    page-1.md
                    ...
"""

from __future__ import annotations

import asyncio
import json
import pickle
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder
from terrain.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord
from terrain.domains.upper.rag.cypher_generator import CypherGenerator
from terrain.domains.upper.rag.llm_backend import create_llm_backend
from terrain.foundation.services.git_service import GitChangeDetector as _GCD
from terrain.foundation.services.kuzu_service import KuzuIngestor
from terrain.domains.core.search.semantic_search import SemanticSearchService
from terrain.entrypoints.mcp.file_editor import FileEditor
from terrain.entrypoints.mcp.pipeline import (
    ProgressCb,
    _collect_todo_funcs,
    artifact_dir_for,
    build_graph,
    build_vector_index,
    enhance_api_docs_step,
    generate_api_docs_step,
    generate_descriptions_step,
    run_wiki_generation,
    save_meta,
    validate_api_docs,
)


def summarize_api_doc(full_doc: str) -> str:
    """Produce a compact summary of an L3 API doc for find_api results.

    Keeps:  header (title, description, metadata), call tree, callers list
    Strips: ## 使用示例 (usage examples), ## 实现 (source code), ## 参数与内存,
            ## 描述 (full description — already captured in header blockquote)

    The caller can still retrieve the full doc via ``get_api_doc`` when
    they need the complete source code or usage examples.
    """
    # Section headers that should be REMOVED to save context
    _HEAVY_SECTIONS = {"## 使用示例", "## 实现", "## 参数与内存", "## 描述"}

    output_lines: list[str] = []
    skip = False

    for line in full_doc.splitlines():
        # Detect section boundary
        if line.startswith("## "):
            # Check if this section should be skipped
            section_prefix = line.split("(")[0].strip()  # "## 被调用 (3)" → "## 被调用"
            if any(section_prefix.startswith(h) for h in _HEAVY_SECTIONS):
                skip = True
                continue
            else:
                skip = False

        if not skip:
            output_lines.append(line)

    # Strip trailing blank lines
    while output_lines and not output_lines[-1].strip():
        output_lines.pop()

    summary = "\n".join(output_lines)

    # Append a hint for the agent
    summary += (
        "\n\n> 💡 Use `get_api_doc` with this function's qualified name "
        "to see full source code, usage examples, and parameter details."
    )
    return summary


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolError(Exception):
    """Error raised by tool handlers.

    The MCP framework catches exceptions and returns ``CallToolResult`` with
    ``isError=True``, so the agent can detect errors via the protocol-level
    flag instead of having to parse JSON response bodies.
    """

    def __init__(self, error_data: dict[str, Any] | str) -> None:
        if isinstance(error_data, str):
            error_data = {"error": error_data}
        self.error_data = error_data
        super().__init__(json.dumps(error_data, ensure_ascii=False, default=str))


class _CompatUnpickler(pickle.Unpickler):
    """Unpickler that redirects old module paths to new ones after the
    harness refactor.  Without this, ``vectors.pkl`` files created by
    pre-0.30 versions fail with ``ModuleNotFoundError`` because pickle
    stores the fully-qualified class path at serialization time.
    """

    _RENAMES: dict[tuple[str, str], tuple[str, str]] = {
        # Pre-2.0 paths (code_graph_builder → terrain rename)
        ("code_graph_builder.domains.core.embedding.vector_store", "MemoryVectorStore"):
            ("terrain.domains.core.embedding.vector_store", "MemoryVectorStore"),
        ("code_graph_builder.domains.core.embedding.vector_store", "VectorRecord"):
            ("terrain.domains.core.embedding.vector_store", "VectorRecord"),
        ("code_graph_builder.domains.core.embedding.vector_store", "SearchResult"):
            ("terrain.domains.core.embedding.vector_store", "SearchResult"),
        ("code_graph_builder.domains.core.search.semantic_search", "SemanticSearchService"):
            ("terrain.domains.core.search.semantic_search", "SemanticSearchService"),
        # Pre-0.30 code_graph_builder paths (flat layout)
        ("code_graph_builder.embeddings.vector_store", "MemoryVectorStore"):
            ("terrain.domains.core.embedding.vector_store", "MemoryVectorStore"),
        ("code_graph_builder.embeddings.vector_store", "VectorRecord"):
            ("terrain.domains.core.embedding.vector_store", "VectorRecord"),
        ("code_graph_builder.embeddings.vector_store", "SearchResult"):
            ("terrain.domains.core.embedding.vector_store", "SearchResult"),
        ("code_graph_builder.embedding.vector_store", "MemoryVectorStore"):
            ("terrain.domains.core.embedding.vector_store", "MemoryVectorStore"),
        ("code_graph_builder.embedding.vector_store", "VectorRecord"):
            ("terrain.domains.core.embedding.vector_store", "VectorRecord"),
        ("code_graph_builder.tools.semantic_search", "SemanticSearchService"):
            ("terrain.domains.core.search.semantic_search", "SemanticSearchService"),
        # Pre-harness terrain paths (terrain flat layout before domains/ refactor)
        ("terrain.embeddings.vector_store", "MemoryVectorStore"):
            ("terrain.domains.core.embedding.vector_store", "MemoryVectorStore"),
        ("terrain.embeddings.vector_store", "VectorRecord"):
            ("terrain.domains.core.embedding.vector_store", "VectorRecord"),
        ("terrain.embeddings.vector_store", "SearchResult"):
            ("terrain.domains.core.embedding.vector_store", "SearchResult"),
        ("terrain.embedding.vector_store", "MemoryVectorStore"):
            ("terrain.domains.core.embedding.vector_store", "MemoryVectorStore"),
        ("terrain.embedding.vector_store", "VectorRecord"):
            ("terrain.domains.core.embedding.vector_store", "VectorRecord"),
        ("terrain.tools.semantic_search", "SemanticSearchService"):
            ("terrain.domains.core.search.semantic_search", "SemanticSearchService"),
    }

    def find_class(self, module: str, name: str):
        key = (module, name)
        if key in self._RENAMES:
            module, name = self._RENAMES[key]
        return super().find_class(module, name)


def _load_vector_store(vectors_path: Path) -> MemoryVectorStore:
    """Load MemoryVectorStore from a pickle cache file."""
    if not vectors_path.exists():
        raise FileNotFoundError(f"Vectors file not found: {vectors_path}")

    with open(vectors_path, "rb") as fh:
        data = _CompatUnpickler(fh).load()

    if isinstance(data, dict) and "vector_store" in data:
        store = data["vector_store"]
        if isinstance(store, MemoryVectorStore):
            return store
        raise RuntimeError(
            f"'vector_store' key found but value is not MemoryVectorStore: {type(store)}"
        )

    if not isinstance(data, list) or len(data) == 0:
        raise RuntimeError(
            f"Unexpected vectors file content: expected non-empty list, got {type(data)}"
        )

    first = data[0]
    if isinstance(first, VectorRecord):
        dimension = len(first.embedding)
        store = MemoryVectorStore(dimension=dimension)
        store.store_embeddings_batch(data)
        return store

    if isinstance(first, dict) and "embedding" in first:
        dimension = len(first["embedding"])
        store = MemoryVectorStore(dimension=dimension)
        for idx, item in enumerate(data):
            store.store_embedding(
                node_id=item.get("node_id", idx),
                qualified_name=item.get("qualified_name", str(idx)),
                embedding=item["embedding"],
                metadata={
                    k: v
                    for k, v in item.items()
                    if k not in ("node_id", "qualified_name", "embedding")
                    and isinstance(v, (str, int, float, type(None)))
                },
            )
        return store

    raise RuntimeError(
        f"Unrecognised vectors file format. First element type: {type(first)}"
    )


class _PipelineTimeout(Exception):
    """Raised when a pipeline step exceeds its timeout."""
    def __init__(self, step_name: str, elapsed: float):
        self.step_name = step_name
        self.elapsed = elapsed
        super().__init__(f"Step '{step_name}' timed out after {elapsed:.0f}s")


def _resolve_artifact_dir(ws_artifact_dir: Path) -> Path:
    """Return the best artifact directory: prefer {repo_path}/.terrain/ over workspace.

    Reads meta.json from *ws_artifact_dir* to discover ``repo_path``, then
    checks whether ``{repo_path}/.terrain/graph.db`` exists.  If it does, return
    the ``.terrain/`` path; otherwise return *ws_artifact_dir* unchanged.
    """
    meta_file = ws_artifact_dir / "meta.json"
    if not meta_file.exists():
        return ws_artifact_dir
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return ws_artifact_dir

    repo_path_str = meta.get("repo_path")
    if not repo_path_str:
        return ws_artifact_dir

    repo_path = Path(repo_path_str)
    if not repo_path.is_dir():
        return ws_artifact_dir

    local_dir = repo_path / ".terrain"
    if (local_dir / "graph.db").exists():
        return local_dir

    return ws_artifact_dir


# ---------------------------------------------------------------------------
# extract_predicates helpers (slice 1/3 of JER-47).
#
# We re-parse C source on demand rather than carrying AST state through the
# graph. Parser construction is expensive (loads tree-sitter shared libs), so
# the parser + query pair for C is cached at module scope.
# ---------------------------------------------------------------------------

_EXTRACT_PREDICATES_C_EXTS: tuple[str, ...] = (".c", ".h")
_EXTRACT_PREDICATES_CACHE: dict[str, Any] | None = None


def _extract_predicates_bundle() -> dict[str, Any]:
    """Return a cached {parser, predicate_query, function_query} bundle for C."""
    global _EXTRACT_PREDICATES_CACHE
    if _EXTRACT_PREDICATES_CACHE is None:
        from terrain.foundation.parsers.parser_loader import load_parsers
        from terrain.foundation.types import constants as cs

        parsers, queries = load_parsers()
        c_queries = queries.get(cs.SupportedLanguage.C) or {}
        _EXTRACT_PREDICATES_CACHE = {
            "parser": parsers.get(cs.SupportedLanguage.C),
            "predicate_query": c_queries.get(cs.QUERY_PREDICATES),
            "function_query": c_queries.get(cs.QUERY_FUNCTIONS),
            "call_query": c_queries.get(cs.QUERY_CALLS),
        }
    return _EXTRACT_PREDICATES_CACHE


def _extract_predicates_list_c_files(repo: Path) -> list[Path]:
    """Return sorted absolute paths of .c / .h files under *repo*."""
    files: list[Path] = []
    seen: set[Path] = set()
    for ext in _EXTRACT_PREDICATES_C_EXTS:
        for p in repo.rglob(f"*{ext}"):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            files.append(p)
    files.sort()
    return files


def _extract_predicates_function_name(func_node) -> str | None:
    """Extract the simple name of a C ``function_definition`` node."""
    declarator = func_node.child_by_field_name("declarator")
    # Unwrap pointer_declarator / parenthesized_declarator layers.
    while declarator is not None and declarator.type not in (
        "identifier",
        "field_identifier",
        "qualified_identifier",
    ):
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            break
        declarator = inner
    if declarator is None or declarator.type not in ("identifier", "field_identifier"):
        return None
    text = declarator.text
    if text is None:
        return None
    return text.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Variable-usage helpers (powering the AST-level mode of find_symbol_in_docs).
#
# These walk the tree-sitter AST of every C/C++ source file under the active
# repository to (a) resolve a short or qualified symbol name to exactly one
# variable declaration and (b) list every read/write usage. Variable nodes
# are not indexed in the graph, so everything is computed on demand.
# ---------------------------------------------------------------------------

# Source-file extensions we scan for variable declarations/usages.
_SYMBOL_USAGE_C_EXTS: dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
}

# Parser cache — load_parsers() is expensive (reads tree-sitter shared libs),
# so cache the C/C++ parsers across calls.
_SYMBOL_USAGE_PARSERS: dict[str, Any] | None = None


def _symbol_usage_get_parsers() -> dict[str, Any]:
    global _SYMBOL_USAGE_PARSERS
    if _SYMBOL_USAGE_PARSERS is None:
        from terrain.foundation.parsers.parser_loader import load_parsers
        from terrain.foundation.types import constants as cs

        parsers, _queries = load_parsers()
        _SYMBOL_USAGE_PARSERS = {
            "c": parsers.get(cs.SupportedLanguage.C),
            "cpp": parsers.get(cs.SupportedLanguage.CPP),
        }
    return _SYMBOL_USAGE_PARSERS


def _symbol_usage_module_qn(repo_path: Path, file_path: Path) -> str:
    rel = file_path.relative_to(repo_path)
    return ".".join([repo_path.name, *rel.with_suffix("").parts])


def _symbol_usage_enclosing_function(node) -> tuple[str, int] | None:
    """Walk up to the nearest function_definition; return (name, start_line)."""
    parent = node.parent
    while parent is not None:
        if parent.type == "function_definition":
            declarator = parent.child_by_field_name("declarator")
            # Unwrap pointer_declarator / parenthesized_declarator layers.
            name_node = declarator
            while name_node is not None and name_node.type not in (
                "identifier",
                "field_identifier",
                "qualified_identifier",
            ):
                inner = name_node.child_by_field_name("declarator")
                if inner is None:
                    break
                name_node = inner
            if name_node is not None and name_node.type in (
                "identifier",
                "field_identifier",
            ):
                return (
                    name_node.text.decode("utf-8", errors="replace"),
                    parent.start_point[0] + 1,
                )
            return ("", parent.start_point[0] + 1)
        parent = parent.parent
    return None


def _symbol_usage_is_static_decl(decl_node) -> bool:
    """Check whether a `declaration` node carries a `static` storage class."""
    for i in range(decl_node.named_child_count):
        child = decl_node.named_child(i)
        if child.type == "storage_class_specifier":
            if child.text.decode("utf-8", errors="replace").strip() == "static":
                return True
    return False


def _symbol_usage_declarator_identifier(declarator_node):
    """Unwrap pointer/array/function layers to reach the inner identifier."""
    cur = declarator_node
    while cur is not None:
        if cur.type == "identifier":
            return cur
        if cur.type == "function_declarator":
            # This is a function, not a variable — bail out.
            return None
        inner = cur.child_by_field_name("declarator")
        if inner is None:
            return None
        cur = inner
    return None


def _symbol_usage_collect_declarations(
    root,
    simple_name: str,
    module_qn: str,
    path: str,
) -> list[dict[str, Any]]:
    """Return all declarations of ``simple_name`` found under ``root``.

    Each entry carries ``kind`` — one of ``global``, ``static_local``,
    ``enum``, ``typedef``, ``function``, ``param``. Only ``global`` and
    ``static_local`` are accepted as variable targets further up the stack.
    """
    out: list[dict[str, Any]] = []

    def _qn(base: str, name: str) -> str:
        return f"{base}.{name}"

    def _visit(node):
        ntype = node.type

        if ntype == "enumerator":
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.text.decode(
                "utf-8", errors="replace"
            ) == simple_name:
                out.append({
                    "kind": "enum",
                    "name": simple_name,
                    "qualified_name": _qn(module_qn, simple_name),
                    "path": path,
                    "line": name_node.start_point[0] + 1,
                })

        elif ntype == "type_definition":
            # typedef ... <name>;
            for i in range(node.named_child_count):
                child = node.named_child(i)
                ident = _symbol_usage_declarator_identifier(child)
                if ident is not None and ident.text.decode(
                    "utf-8", errors="replace"
                ) == simple_name:
                    out.append({
                        "kind": "typedef",
                        "name": simple_name,
                        "qualified_name": _qn(module_qn, simple_name),
                        "path": path,
                        "line": ident.start_point[0] + 1,
                    })
                    break

        elif ntype == "function_definition":
            declarator = node.child_by_field_name("declarator")
            # Unwrap to function_declarator → identifier.
            cur = declarator
            while cur is not None and cur.type != "function_declarator":
                cur = cur.child_by_field_name("declarator")
            if cur is not None:
                name_node = cur.child_by_field_name("declarator")
                if (
                    name_node is not None
                    and name_node.type == "identifier"
                    and name_node.text.decode("utf-8", errors="replace") == simple_name
                ):
                    out.append({
                        "kind": "function",
                        "name": simple_name,
                        "qualified_name": _qn(module_qn, simple_name),
                        "path": path,
                        "line": name_node.start_point[0] + 1,
                    })

        elif ntype == "declaration":
            # Could be a variable declaration or a function prototype.
            declarator_field = node.child_by_field_name("declarator")
            declarators = []
            # A declaration may have multiple init_declarator children
            # (e.g. `int a, b;`), but tree-sitter exposes only one via
            # ``declarator``. Walk named children of type init_declarator or
            # identifier to find all candidates.
            for i in range(node.named_child_count):
                child = node.named_child(i)
                if child.type in ("init_declarator", "identifier", "pointer_declarator", "array_declarator"):
                    declarators.append(child)
            if declarator_field is not None and declarator_field not in declarators:
                declarators.append(declarator_field)

            is_static = _symbol_usage_is_static_decl(node)
            enclosing = _symbol_usage_enclosing_function(node)

            for d in declarators:
                # Skip function prototypes (declarator tree contains function_declarator).
                if d.type == "function_declarator":
                    continue
                # For init_declarator, dig into its .declarator field.
                target = d
                if d.type == "init_declarator":
                    target = d.child_by_field_name("declarator") or d
                ident = _symbol_usage_declarator_identifier(target)
                if ident is None:
                    continue
                if ident.text.decode("utf-8", errors="replace") != simple_name:
                    continue

                if enclosing is None:
                    kind = "global"
                    qn = _qn(module_qn, simple_name)
                else:
                    if is_static:
                        kind = "static_local"
                        qn = f"{module_qn}.{enclosing[0]}.{simple_name}"
                    else:
                        # Non-static local — not a variable symbol worth tracking.
                        continue
                out.append({
                    "kind": kind,
                    "name": simple_name,
                    "qualified_name": qn,
                    "path": path,
                    "line": ident.start_point[0] + 1,
                })

        elif ntype == "parameter_declaration":
            declarator = node.child_by_field_name("declarator")
            ident = _symbol_usage_declarator_identifier(declarator) if declarator else None
            if ident is not None and ident.text.decode(
                "utf-8", errors="replace"
            ) == simple_name:
                out.append({
                    "kind": "param",
                    "name": simple_name,
                    "qualified_name": _qn(module_qn, simple_name),
                    "path": path,
                    "line": ident.start_point[0] + 1,
                })

        for i in range(node.named_child_count):
            _visit(node.named_child(i))

    _visit(root)
    return out


def _symbol_usage_identifier_role(ident_node) -> str:
    """Classify an identifier node as ``decl``, ``write``, ``skip``, or ``read``."""
    parent = ident_node.parent
    if parent is None:
        return "skip"
    ptype = parent.type

    if ptype == "init_declarator":
        if parent.child_by_field_name("declarator") == ident_node:
            return "decl"
    if ptype in ("declaration", "parameter_declaration"):
        if parent.child_by_field_name("declarator") == ident_node:
            return "decl"
    if ptype == "function_declarator":
        if parent.child_by_field_name("declarator") == ident_node:
            return "decl"
    if ptype == "pointer_declarator":
        # part of a declarator unwrap — walk up one more level
        grand = parent.parent
        if grand is not None and grand.type in (
            "init_declarator",
            "declaration",
            "parameter_declaration",
            "function_declarator",
        ):
            if grand.child_by_field_name("declarator") == parent:
                return "decl"
    if ptype == "enumerator":
        if parent.child_by_field_name("name") == ident_node:
            return "decl"
    if ptype == "field_expression":
        if parent.child_by_field_name("field") == ident_node:
            return "skip"
        # ``ident`` is the ``argument`` of a field_expression. If the
        # enclosing field_expression chain is the LHS of an assignment or the
        # target of ++/--, the write pass owns this node — don't also report
        # it as a read.
        if parent.child_by_field_name("argument") == ident_node:
            chain_top = parent
            while (
                chain_top.parent is not None
                and chain_top.parent.type == "field_expression"
                and chain_top.parent.child_by_field_name("argument") == chain_top
            ):
                chain_top = chain_top.parent
            gp = chain_top.parent
            if gp is not None:
                if (
                    gp.type == "assignment_expression"
                    and gp.child_by_field_name("left") == chain_top
                ):
                    return "skip"
                if (
                    gp.type == "update_expression"
                    and gp.child_by_field_name("argument") == chain_top
                ):
                    return "skip"
    if ptype == "call_expression":
        if parent.child_by_field_name("function") == ident_node:
            return "skip"
    if ptype == "assignment_expression":
        if parent.child_by_field_name("left") == ident_node:
            return "write"
    if ptype == "update_expression":
        if parent.child_by_field_name("argument") == ident_node:
            return "write"
    if ptype == "pointer_expression":
        op = parent.child_by_field_name("operator")
        if op is not None and op.text.decode("utf-8", errors="replace") == "&":
            return "skip"

    return "read"


def _symbol_usage_collect_reads(
    root,
    simple_name: str,
    source_lines: list[str],
    module_qn: str,
    path: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def _visit(node):
        if node.type == "identifier" and node.text.decode(
            "utf-8", errors="replace"
        ) == simple_name:
            role = _symbol_usage_identifier_role(node)
            if role == "read":
                line_no = node.start_point[0] + 1
                enclosing = _symbol_usage_enclosing_function(node)
                enclosing_qn = (
                    f"{module_qn}.{enclosing[0]}" if enclosing else module_qn
                )
                context = ""
                if 0 < line_no <= len(source_lines):
                    raw = source_lines[line_no - 1].strip()
                    context = raw if len(raw) <= 200 else raw[:199] + "…"
                out.append({
                    "mode": "read",
                    "location": f"{path}:{line_no}",
                    "enclosing_function": enclosing_qn,
                    "context": context,
                })
        for i in range(node.named_child_count):
            _visit(node.named_child(i))

    _visit(root)
    return out


# Compound assignment operators recognised by slice 2. The `=` plain direct
# assignment is handled separately.
_SYMBOL_USAGE_COMPOUND_OPS: frozenset[str] = frozenset(
    {"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
)


def _symbol_usage_lhs_matches(left_node, simple_name: str) -> bool:
    """Does the LHS of an assignment (or target of ++/--) refer to ``simple_name``?

    Matches three shapes (MVP):
      * bare identifier ``simple_name``
      * ``field_expression`` whose outermost ``argument`` is an identifier
        matching ``simple_name`` (e.g. ``g_alarm.dci`` with symbol=``g_alarm``).
      * ``subscript_expression`` whose outermost ``argument`` is an identifier
        matching ``simple_name`` (e.g. ``g_array[i]`` with symbol=``g_array``).
    """
    if left_node is None:
        return False
    if left_node.type == "identifier":
        return left_node.text.decode("utf-8", errors="replace") == simple_name
    if left_node.type in ("field_expression", "subscript_expression"):
        arg = left_node.child_by_field_name("argument")
        # Walk down any inner field/subscript chain to find the outermost argument.
        while arg is not None and arg.type in ("field_expression", "subscript_expression"):
            arg = arg.child_by_field_name("argument")
        if arg is not None and arg.type == "identifier":
            return arg.text.decode("utf-8", errors="replace") == simple_name
    return False


def _symbol_usage_is_address_of(node, simple_name: str) -> bool:
    """Return True iff *node* is a ``pointer_expression`` of the form ``&simple_name``."""
    if node is None or node.type != "pointer_expression":
        return False
    op = node.child_by_field_name("operator")
    if op is None or op.text.decode("utf-8", errors="replace") != "&":
        return False
    arg = node.child_by_field_name("argument")
    if arg is None or arg.type != "identifier":
        return False
    return arg.text.decode("utf-8", errors="replace") == simple_name


def _symbol_usage_collect_aliases(func_node, simple_name: str) -> frozenset[str]:
    """Find local pointer variables aliased to ``&simple_name`` within a function.

    Recognises two MVP patterns inside the function body:
      * ``T *p = &simple_name;`` (init_declarator)
      * ``p = &simple_name;``    (plain assignment, p was declared earlier)

    Returns the set of local variable names whose value points at ``simple_name``.
    Flow-insensitive: any later reassignment of ``p`` is ignored — accepted false
    positive per slice-3 MVP scope.
    """
    aliases: set[str] = set()

    def _visit(node):
        ntype = node.type
        if ntype == "init_declarator":
            value = node.child_by_field_name("value")
            if value is not None and _symbol_usage_is_address_of(value, simple_name):
                decl = node.child_by_field_name("declarator")
                ident = _symbol_usage_declarator_identifier(decl) if decl is not None else None
                if ident is not None:
                    aliases.add(ident.text.decode("utf-8", errors="replace"))
        elif ntype == "assignment_expression":
            op_node = node.child_by_field_name("operator")
            op_text = op_node.text.decode("utf-8", errors="replace") if op_node is not None else "="
            if op_text == "=":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if (
                    left is not None
                    and left.type == "identifier"
                    and _symbol_usage_is_address_of(right, simple_name)
                ):
                    aliases.add(left.text.decode("utf-8", errors="replace"))
        for i in range(node.named_child_count):
            _visit(node.named_child(i))

    _visit(func_node)
    return frozenset(aliases)


def _symbol_usage_collect_writes(
    root,
    simple_name: str,
    source_lines: list[str],
    module_qn: str,
    path: str,
) -> list[dict[str, Any]]:
    """Collect write sites for ``simple_name`` from one parsed source tree.

    Recognises:
      * ``assignment_expression`` with ``=`` (``assign_type="direct"``)
      * ``assignment_expression`` with a compound operator (``+=``, ``|=``, ...)
        (``assign_type="compound"``)
      * ``update_expression`` (``x++`` / ``++x`` / ``x--`` / ``--x``)
        (``assign_type="compound"``, rhs="")
      * ``call_expression`` of a memcpy-family function with first arg
        ``&simple_name`` or bare array name (``assign_type="via_memcpy"``)
      * ``call_expression`` of a non-readonly function with any
        ``&simple_name`` argument (``assign_type="address_of"``)
      * ``assignment_expression`` whose left is ``*p`` where ``p`` is a local
        alias of ``&simple_name`` (``assign_type="pointer_deref_write"``)
    """
    from terrain.foundation.types import constants as cs

    out: list[dict[str, Any]] = []

    def _enclosing_qn(node) -> str:
        enc = _symbol_usage_enclosing_function(node)
        return f"{module_qn}.{enc[0]}" if enc else module_qn

    def _ctx(line_no: int) -> str:
        if 0 < line_no <= len(source_lines):
            raw = source_lines[line_no - 1].strip()
            return raw if len(raw) <= 200 else raw[:199] + "…"
        return ""

    def _emit_pointer_deref_write(node):
        """``*p = rhs`` with ``p`` aliasing ``simple_name`` → write entry."""
        op_node = node.child_by_field_name("operator")
        right = node.child_by_field_name("right")
        op = op_node.text.decode("utf-8", errors="replace") if op_node else "="
        rhs = (
            right.text.decode("utf-8", errors="replace").strip()
            if right is not None
            else ""
        )
        line_no = node.start_point[0] + 1
        out.append({
            "mode": "write",
            "location": f"{path}:{line_no}",
            "enclosing_function": _enclosing_qn(node),
            "context": _ctx(line_no),
            "assign_type": "pointer_deref_write",
            "op": op,
            "rhs": rhs,
        })

    def _emit_call_write(node, fname: str, assign_type: str):
        line_no = node.start_point[0] + 1
        rhs = node.text.decode("utf-8", errors="replace").strip()
        out.append({
            "mode": "write",
            "location": f"{path}:{line_no}",
            "enclosing_function": _enclosing_qn(node),
            "context": _ctx(line_no),
            "assign_type": assign_type,
            "op": fname,
            "rhs": rhs,
        })

    def _handle_call(node):
        """Detect via_memcpy / address_of writes triggered by a ``call_expression``."""
        func = node.child_by_field_name("function")
        args_list = node.child_by_field_name("arguments")
        if func is None or args_list is None or func.type != "identifier":
            return
        fname = func.text.decode("utf-8", errors="replace")
        if fname in cs.READONLY_API_FUNCTIONS:
            return
        # Iterate positional arguments (skip comments / non-named children).
        args = [args_list.named_child(i) for i in range(args_list.named_child_count)]
        for idx, arg in enumerate(args):
            is_addr_of = _symbol_usage_is_address_of(arg, simple_name)
            is_bare_array = (
                arg.type == "identifier"
                and arg.text.decode("utf-8", errors="replace") == simple_name
            )
            if (
                idx == 0
                and fname in cs.MEMCPY_LIKE_FUNCTIONS
                and (is_addr_of or is_bare_array)
            ):
                _emit_call_write(node, fname, "via_memcpy")
                return
            if is_addr_of:
                _emit_call_write(node, fname, "address_of")
                return

    def _visit(node, alias_set: frozenset[str]):
        ntype = node.type

        # Re-scope aliases when entering a function definition.
        if ntype == "function_definition":
            new_aliases = _symbol_usage_collect_aliases(node, simple_name)
            for i in range(node.named_child_count):
                _visit(node.named_child(i), new_aliases)
            return

        if ntype == "assignment_expression":
            left = node.child_by_field_name("left")
            # 1. pointer_deref_write: `*p = rhs` with p in alias_set
            if left is not None and left.type == "pointer_expression":
                op_n = left.child_by_field_name("operator")
                argn = left.child_by_field_name("argument")
                if (
                    op_n is not None
                    and op_n.text.decode("utf-8", errors="replace") == "*"
                    and argn is not None
                    and argn.type == "identifier"
                ):
                    pname = argn.text.decode("utf-8", errors="replace")
                    if pname in alias_set:
                        _emit_pointer_deref_write(node)
            # 2. direct / compound assignment of simple_name
            elif _symbol_usage_lhs_matches(left, simple_name):
                op_node = node.child_by_field_name("operator")
                right = node.child_by_field_name("right")
                op = (
                    op_node.text.decode("utf-8", errors="replace")
                    if op_node is not None
                    else "="
                )
                if op == "=":
                    assign_type = "direct"
                elif op in _SYMBOL_USAGE_COMPOUND_OPS:
                    assign_type = "compound"
                else:
                    assign_type = "compound"
                rhs = (
                    right.text.decode("utf-8", errors="replace").strip()
                    if right is not None
                    else ""
                )
                line_no = node.start_point[0] + 1
                out.append({
                    "mode": "write",
                    "location": f"{path}:{line_no}",
                    "enclosing_function": _enclosing_qn(node),
                    "context": _ctx(line_no),
                    "assign_type": assign_type,
                    "op": op,
                    "rhs": rhs,
                })
        elif ntype == "update_expression":
            arg = node.child_by_field_name("argument")
            if (
                arg is not None
                and arg.type == "identifier"
                and arg.text.decode("utf-8", errors="replace") == simple_name
            ):
                op_node = node.child_by_field_name("operator")
                op = (
                    op_node.text.decode("utf-8", errors="replace")
                    if op_node is not None
                    else "++"
                )
                line_no = node.start_point[0] + 1
                out.append({
                    "mode": "write",
                    "location": f"{path}:{line_no}",
                    "enclosing_function": _enclosing_qn(node),
                    "context": _ctx(line_no),
                    "assign_type": "compound",
                    "op": op,
                    "rhs": "",
                })
        elif ntype == "call_expression":
            _handle_call(node)

        for i in range(node.named_child_count):
            _visit(node.named_child(i), alias_set)

    _visit(root, frozenset())
    return out


def _symbol_usage_collect_scopes(root, module_qn: str) -> set[str]:
    """Return every valid ``qualified_scope`` rooted at this file.

    Includes the file's own module qn plus ``<module>.<func>`` for each
    top-level function definition. MVP does not expand member functions or
    namespaces — the handler's scope matcher also accepts suffix segments,
    which covers the cases named in the issue without a deeper AST walk.
    """
    scopes: set[str] = {module_qn}

    def _visit(node):
        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            cur = declarator
            while cur is not None and cur.type != "function_declarator":
                cur = cur.child_by_field_name("declarator")
            if cur is not None:
                name_node = cur.child_by_field_name("declarator")
                while name_node is not None and name_node.type not in (
                    "identifier",
                    "field_identifier",
                ):
                    inner = name_node.child_by_field_name("declarator")
                    if inner is None:
                        break
                    name_node = inner
                if name_node is not None and name_node.type in (
                    "identifier",
                    "field_identifier",
                ):
                    scopes.add(
                        f"{module_qn}.{name_node.text.decode('utf-8', errors='replace')}"
                    )
        for i in range(node.named_child_count):
            _visit(node.named_child(i))

    _visit(root)
    return scopes


def _symbol_usage_list_source_files(repo: Path) -> list[tuple[Path, str]]:
    """Return sorted list of (abs_path, language_key) for C/C++ sources in repo."""
    files: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for ext, lang in _SYMBOL_USAGE_C_EXTS.items():
        for p in repo.rglob(f"*{ext}"):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            files.append((p, lang))
    files.sort(key=lambda t: t[0])
    return files


class MCPToolsRegistry:
    """Registry that manages workspace-based repo services and tool handlers."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._workspace.mkdir(parents=True, exist_ok=True)

        self._ingestor: KuzuIngestor | None = None
        self._cypher_gen: CypherGenerator | None = None
        self._semantic_service: SemanticSearchService | None = None
        self._file_editor: FileEditor | None = None
        self._active_repo_path: Path | None = None
        self._active_artifact_dir: Path | None = None

        self._try_auto_load()

    def _try_auto_load(self) -> None:
        """Try to load the last active repo from workspace."""
        active_file = self._workspace / "active.txt"
        if not active_file.exists():
            return
        artifact_dir_name = active_file.read_text(encoding="utf-8", errors="replace").strip()
        artifact_dir = self._workspace / artifact_dir_name
        if artifact_dir.exists():
            artifact_dir = _resolve_artifact_dir(artifact_dir)
            try:
                self._load_services(artifact_dir)
                logger.info(f"Auto-loaded repo from: {artifact_dir}")
            except Exception as exc:
                logger.warning(f"Graph/LLM services unavailable: {exc}")

    def _load_services(self, artifact_dir: Path) -> None:
        """Load KuzuIngestor + CypherGenerator + SemanticSearchService from artifact dir."""
        meta_file = artifact_dir / "meta.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"meta.json not found in {artifact_dir}")

        from terrain.foundation.utils.paths import normalize_repo_path

        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        try:
            raw_repo_path = meta["repo_path"]
        except KeyError as exc:
            raise KeyError(f"meta.json in {artifact_dir} missing 'repo_path'") from exc
        try:
            repo_path = Path(normalize_repo_path(raw_repo_path))
        except (TypeError, ValueError) as exc:
            logger.warning(f"meta.json repo_path normalize failed ({raw_repo_path!r}): {exc}; falling back to raw")
            repo_path = Path(raw_repo_path)
        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"

        self.close()
        self._active_repo_path = repo_path
        self._active_artifact_dir = artifact_dir
        try:
            self._file_editor = FileEditor(repo_path)
        except Exception as exc:
            logger.warning(f"File editor unavailable: {exc}")

        # Note: We don't keep a persistent ingestor connection open anymore
        # to avoid file locks. Each tool that needs Kuzu will create a temporary
        # connection and close it after use.
        self._db_path = db_path

        llm = create_llm_backend()
        cypher_gen: CypherGenerator | None = None
        if llm.available:
            cypher_gen = CypherGenerator(llm)
        else:
            logger.warning("LLM not configured — query_code_graph will be unavailable")

        # Load semantic search service without graph_service (Kuzu) dependency
        # find_api will work with vector search only, avoiding Kuzu file locks
        semantic_service: SemanticSearchService | None = None
        if vectors_path.exists():
            try:
                vector_store = _load_vector_store(vectors_path)
                from terrain.domains.core.embedding.qwen3_embedder import create_embedder
                embedder = create_embedder(batch_size=10)
                semantic_service = SemanticSearchService(
                    embedder=embedder,
                    vector_store=vector_store,
                    graph_service=None,  # No Kuzu dependency for find_api
                )
                logger.info(f"Loaded vector store: {vector_store.get_stats()}")
            except Exception as exc:
                logger.warning(
                    f"Semantic search unavailable: {exc}. "
                    "Check DASHSCOPE_API_KEY or EMBEDDING_API_KEY / OPENAI_API_KEY."
                )

        self._cypher_gen = cypher_gen
        self._semantic_service = semantic_service

    def _set_active(self, artifact_dir: Path) -> None:
        """Mark artifact_dir as active in workspace."""
        (self._workspace / "active.txt").write_text(
            artifact_dir.name, encoding="utf-8"
        )

    def close(self) -> None:
        if self._ingestor is not None:
            try:
                self._ingestor.__exit__(None, None, None)
            except Exception:
                pass
            self._ingestor = None
        self._file_editor = None

    @contextmanager
    def _temporary_ingestor(self):
        """Context manager for temporary Kuzu ingestor connection.

        Usage:
            with self._temporary_ingestor() as ingestor:
                # Use ingestor for queries
                rows = ingestor.query(...)
            # Connection automatically closed here
        """
        if self._active_artifact_dir is None:
            raise ToolError("No active repository. Run `terrain index <path>` first.")

        db_path = self._active_artifact_dir / "graph.db"
        if not db_path.exists():
            raise ToolError("Graph database not found. Run `terrain index <path>` first.")

        ingestor = KuzuIngestor(db_path, read_only=True)
        try:
            ingestor.__enter__()
            yield ingestor
        finally:
            ingestor.__exit__(None, None, None)

    def _require_active(self) -> None:
        """Raise :class:`ToolError` when no repository has been indexed."""
        if self._active_artifact_dir is None:
            raise ToolError("No repository indexed yet. Run `terrain index <path>` first.")

    def _require_repo_path(self) -> None:
        """Raise :class:`ToolError` when no repository path is set."""
        if self._active_repo_path is None:
            raise ToolError("No repository path set. Run `terrain index <path>` first.")

    @property
    def active_state(self) -> tuple[Path, Path] | None:
        """Return (repo_path, artifact_dir) for the currently active repo, or None."""
        if self._active_repo_path is not None and self._active_artifact_dir is not None:
            return self._active_repo_path, self._active_artifact_dir
        return None

    def tools(self) -> list[ToolDefinition]:
        defs: list[ToolDefinition] = [
            # NOTE: initialize_repository is intentionally hidden from the tool
            # list — users should index via `terrain index`.  The handler is kept so
            # internal callers and the incremental-sync mechanism still work.
            ToolDefinition(
                name="get_repository_info",
                description=(
                    "Return information about the currently active (indexed) repository, "
                    "including graph statistics (node/relationship counts), wiki pages, "
                    "and service availability."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="list_repositories",
                description=(
                    "List all previously indexed repositories in the workspace. "
                    "Shows repo name, path, last indexed time, which pipeline steps "
                    "have been completed (graph, api_docs, embeddings, wiki), and "
                    "which one is currently active.\n\n"
                    "Each repo also carries a `staleness` field: "
                    "'up-to-date' (HEAD matches the indexed commit), "
                    "'stale' (HEAD has moved — graph results may be out of date), "
                    "or 'unknown' (not a git repo, HEAD unreadable, or graph not built). "
                    "Short SHAs are reported as `indexed_head` and `current_head` so you "
                    "can tell how far the repo has drifted. "
                    "`commits_since` gives the integer number of commits HEAD is ahead of "
                    "indexed_head; it is null when staleness is 'unknown'. "
                    "Consult staleness before calling switch_repository or running heavy "
                    "queries; prefer re-indexing stale repos for trustworthy results."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="switch_repository",
                description=(
                    "Switch the active repository to a previously indexed one. "
                    "After switching, all query tools (query_code_graph, semantic_search, "
                    "list_wiki_pages, etc.) will operate on the selected repo. "
                    "Use list_repositories first to see available repos."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "repo_name": {
                            "type": "string",
                            "description": (
                                "Repository name or artifact directory name "
                                "(e.g. 'my-project' or 'my-project_a1b2c3d4'). "
                                "Use list_repositories to see available names."
                            ),
                        },
                    },
                    "required": ["repo_name"],
                },
            ),
            # link_repository: handler preserved, exposed only via CLI.
            # -----------------------------------------------------------------
            # Core query tools: fuzzy locate → browse → deep dive
            # -----------------------------------------------------------------
            ToolDefinition(
                name="find_api",
                description=(
                    "ALWAYS call this first when the user asks about codebase "
                    "functionality, features, or how something works. "
                    "Combines semantic search with API doc lookup — returns matching "
                    "functions with signatures, docstrings, and call graphs."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of the API to find.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results. Default: 5.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            # list_api_docs: handler preserved, not exposed to MCP clients.
            # find_api covers the primary use case.
            ToolDefinition(
                name="get_api_doc",
                description=(
                    "Read the detailed L3 API documentation for a specific function. "
                    "Includes signature, description, full call tree (callees with depth), "
                    "caller list with locations, real usage examples extracted from "
                    "the codebase, parameter ownership, and source code implementation. "
                    "Use this to understand how to call a function and how to combine "
                    "it with other APIs."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "qualified_name": {
                            "type": "string",
                            "description": (
                                "Fully qualified function name "
                                "(e.g. 'project.api.api_init')."
                            ),
                        },
                    },
                    "required": ["qualified_name"],
                },
            ),
            # generate_api_docs: handler preserved, use `terrain index` instead.
            # -----------------------------------------------------------------
            # Configuration / diagnostics
            # -----------------------------------------------------------------
            ToolDefinition(
                name="get_config",
                description=(
                    "Show current MCP server configuration: LLM provider, model, "
                    "embedding provider, workspace path, and service availability. "
                    "Useful for debugging connection issues or verifying setup."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            # reload_config: handler preserved, not exposed to MCP clients.
            # -----------------------------------------------------------------
            # Call graph queries
            # -----------------------------------------------------------------
            ToolDefinition(
                name="find_callers",
                description=(
                    "Find all functions that call a specific function (i.e. its "
                    "callers / references). Accepts a qualified name like "
                    "'module.Class.method' or a simple name like 'parse_btype'. "
                    "Returns caller qualified names, file paths, and line numbers. "
                    "Does NOT require an LLM — queries the graph directly."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "function_name": {
                            "type": "string",
                            "description": (
                                "Function name or qualified name to search for. "
                                "Examples: 'parse_btype', 'tinycc.tccgen.parse_btype'"
                            ),
                        },
                    },
                    "required": ["function_name"],
                },
            ),
            # -----------------------------------------------------------------
            # Call chain trace
            # -----------------------------------------------------------------
            ToolDefinition(
                name="trace_call_chain",
                description=(
                    "Trace the upward call chain of a target function using BFS. "
                    "Finds all entry points that can reach the target, reconstructs "
                    "every call path, and generates Wiki investigation worksheets.\n\n"
                    "IMPORTANT — THIS TOOL RETURNS status='pending_fill'. "
                    "The response includes 'wiki_content' with raw markdown containing "
                    "<!-- FILL --> placeholders. You MUST:\n"
                    "1. Read the 'wiki_content' in the response (already included).\n"
                    "2. Call get_code_snippet / get_api_doc for each function in the paths.\n"
                    "3. Replace every <!-- FILL --> with real analysis.\n"
                    "4. Write the completed markdown back to the 'wiki_page' file paths.\n"
                    "5. Summarize your findings to the user.\n"
                    "DO NOT just return the raw results to the user — "
                    "the analysis is incomplete until all placeholders are filled."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_function": {
                            "type": "string",
                            "description": (
                                "Target function name — supports simple name "
                                "(e.g. 'LogSaveWithSubId') or fully qualified name "
                                "(e.g. 'pkg.log.LogSaveWithSubId')."
                            ),
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum upward traversal depth. Default: 10.",
                        },
                        "save_wiki": {
                            "type": "boolean",
                            "description": (
                                "Whether to generate a Wiki investigation worksheet. "
                                "Default: true."
                            ),
                        },
                        "paths_per_entry_point": {
                            "type": "integer",
                            "description": (
                                "Maximum number of paths to keep per entry point. "
                                "Default: 20."
                            ),
                        },
                    },
                    "required": ["target_function"],
                },
            ),
            # -----------------------------------------------------------------
            # get_merge_diff: functions changed between two merge commits
            # -----------------------------------------------------------------
            ToolDefinition(
                name="get_merge_diff",
                description=(
                    "Find which functions changed between two merge commits.\n\n"
                    "Useful for understanding what a feature branch introduced or "
                    "for reviewing the scope of a release.\n\n"
                    "When called with no arguments, automatically uses the two most "
                    "recent merge commits in the repository history. You may also "
                    "supply explicit commit SHAs."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "from_merge": {
                            "type": "string",
                            "description": (
                                "Starting merge commit SHA (exclusive). "
                                "Defaults to the second-most-recent merge commit."
                            ),
                        },
                        "to_merge": {
                            "type": "string",
                            "description": (
                                "Ending merge commit SHA (inclusive). "
                                "Defaults to the most-recent merge commit."
                            ),
                        },
                        "branch": {
                            "type": "string",
                            "description": (
                                "Branch name to search for merge commits "
                                "(e.g. 'main', 'origin/main'). "
                                "Defaults to HEAD (current branch)."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            # rebuild_embeddings: handler preserved, use `terrain index` instead.
            # -----------------------------------------------------------------
            # Symbol / global-variable lookup
            # -----------------------------------------------------------------
            ToolDefinition(
                name="find_symbol_in_docs",
                description=(
                    "Find where a symbol is used. Two modes depending on the parameters:\n\n"
                    "1. **Doc-based reference search (default)** — when neither `mode` nor "
                    "`qualified_scope` is supplied. Scans the pre-built API docs for "
                    "'## 全局变量引用' entries (UPPER_CASE identifiers and explicit "
                    "`global` declarations captured at index time). Returns functions "
                    "with their module, file location, and the full '## 全局变量引用' "
                    "section of each match. Use for questions like "
                    "'Where is CONFIG_FILE used?' / 'Which functions read MAX_RETRY?'.\n\n"
                    "2. **AST-level variable read/write usage** — when `mode` or "
                    "`qualified_scope` is supplied. Resolves the symbol against the "
                    "source tree and lists every read/write site for a C/C++ variable "
                    "(global or function-scope static). Accepts a short name "
                    "('g_alarm') or a qualified name ('module.g_alarm' / "
                    "'module.func.counter'). Returns the resolved qualified_name + "
                    "kind plus a list of usages with file:line, the enclosing "
                    "function's qualified name, and the source line. Writes carry "
                    "'assign_type' ('direct' for '=', 'compound' for '+='/'|='/'++'/..., "
                    "'via_memcpy' for memcpy-family destinations, 'address_of' for "
                    "`&sym` passed to a non-readonly function, 'pointer_deref_write' "
                    "for `*p = ...` where `p` aliases `&sym` within the same function), "
                    "the raw 'op', and 'rhs' (full call expression for via_memcpy/"
                    "address_of, empty for ++/--). Rejects enum values, typedefs, "
                    "functions, and parameters with "
                    "error='symbol is not a variable (kind=X)'. qualified_scope "
                    "restricts to one function or module qn (full qn or a dot-segment "
                    "suffix); unknown scopes return error='scope not found: <scope>'."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": (
                                "Name of the symbol to look up "
                                "(e.g. 'CONFIG_FILE', 'g_alarm', "
                                "'proj.alarm.g_alarm')."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                "Doc-based mode only: maximum number of results "
                                "to return. Default: 30."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["read", "write", "all"],
                            "description": (
                                "Opt into AST-level variable usage mode. 'read' "
                                "lists read sites, 'write' lists assignment/update "
                                "sites (direct + compound), 'all' merges both "
                                "sorted by location."
                            ),
                        },
                        "qualified_scope": {
                            "type": "string",
                            "description": (
                                "AST-level mode only: restrict results to a "
                                "function or module qn (full qn like "
                                "'proj.alarm_cfg.AlarmCheck_DCI' or a dot-segment "
                                "suffix like 'alarm_cfg.AlarmCheck_DCI'). "
                                "Supplying this alone also selects AST-level mode."
                            ),
                        },
                    },
                    "required": ["symbol"],
                },
            ),
            # -----------------------------------------------------------------
            # Predicate extraction (slice 1/3 of JER-47)
            # -----------------------------------------------------------------
            ToolDefinition(
                name="extract_predicates",
                description=(
                    "Extract the flat list of predicate nodes inside a C function "
                    "(if / else_if / while / do_while / for / switch_case / ternary).\n\n"
                    "Slice 1 MVP: returns only the skeleton — kind, location, "
                    "expression and nesting_path. Future slices will add "
                    "symbols_referenced, guarded_block, contains_assignments, "
                    "contains_calls, has_early_return.\n\n"
                    "Accepts a short function name ('AlarmCheck_DCI') or a qualified "
                    "name ending with the function name ('pkg.mod.AlarmCheck_DCI'). "
                    "If multiple C functions share the simple name, returns "
                    "success=False with a candidates list so the caller can pass a "
                    "fully-qualified name on retry."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "qualified_name": {
                            "type": "string",
                            "description": (
                                "C function name — simple ('AlarmCheck_DCI') or "
                                "qualified ('proj.alarm.AlarmCheck_DCI'). "
                                "Matching is by last '.'-separated component."
                            ),
                        },
                    },
                    "required": ["qualified_name"],
                },
            ),
            # -----------------------------------------------------------------
            # Hidden tools — handlers preserved, not exposed to MCP clients.
            # Superseded by API-doc-based workflows above.
            #   query_code_graph, get_code_snippet, semantic_search,
            #   locate_function, list_api_interfaces,
            #   list_wiki_pages, get_wiki_page, generate_wiki,
            #   build_graph, prepare_guidance
            # -----------------------------------------------------------------
        ]

        return defs

    def get_handler(self, name: str):
        handlers: dict[str, Any] = {
            "initialize_repository": self._handle_initialize_repository,
            "get_repository_info": self._handle_get_repository_info,
            "list_repositories": self._handle_list_repositories,
            "switch_repository": self._handle_switch_repository,
            "link_repository": self._handle_link_repository,
            "query_code_graph": self._handle_query_code_graph,
            "get_code_snippet": self._handle_get_code_snippet,
            "semantic_search": self._handle_semantic_search,
            "list_wiki_pages": self._handle_list_wiki_pages,
            "get_wiki_page": self._handle_get_wiki_page,
            "locate_function": self._handle_locate_function,
            "list_api_interfaces": self._handle_list_api_interfaces,
            "list_api_docs": self._handle_list_api_docs,
            "get_api_doc": self._handle_get_api_doc,
            "find_api": self._handle_find_api,
            "generate_wiki": self._handle_generate_wiki,
            "rebuild_embeddings": self._handle_rebuild_embeddings,
            "build_graph": self._handle_build_graph,
            "generate_api_docs": self._handle_generate_api_docs,
            "prepare_guidance": self._handle_prepare_guidance,
            "find_callers": self._handle_find_callers,
            "get_config": self._handle_get_config,
            "reload_config": self._handle_reload_config,
            "trace_call_chain": self._handle_trace_call_chain,
            "get_merge_diff": self._handle_get_merge_diff,
            "find_symbol_in_docs": self._handle_find_symbol_in_docs,
            "extract_predicates": self._handle_extract_predicates,
        }
        return handlers.get(name)

    # -------------------------------------------------------------------------
    # initialize_repository — runs the full pipeline in a thread pool
    # -------------------------------------------------------------------------

    async def _handle_initialize_repository(
        self,
        repo_path: str,
        rebuild: bool = True,
        wiki_mode: str = "comprehensive",
        backend: str = "kuzu",
        skip_wiki: bool = False,
        skip_embed: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        # Hot-reload config from .env / settings.json before running the
        # pipeline, so any changes made via --setup or manual edits take
        # effect without restarting the MCP server.
        from terrain.foundation.utils.settings import reload_env
        changes = reload_env(workspace=self._workspace)
        # if changes.get("updated") or changes.get("removed"):
        #     logger.info(f"Config hot-reloaded before initialize: {changes}")

        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise ToolError(f"Repository path does not exist: {repo}")

        loop = asyncio.get_running_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        # Force rebuild to ensure fresh graph data
        effective_rebuild = True

        from terrain.foundation.types.config import TimeoutConfig
        tc = TimeoutConfig.from_env()

        cancel = threading.Event()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._run_pipeline(
                        repo, effective_rebuild, wiki_mode, sync_progress,
                        backend=backend, skip_wiki=skip_wiki, skip_embed=skip_embed,
                        cancel_event=cancel, timeout_cfg=tc,
                    ),
                ),
                timeout=tc.pipeline_total if tc.pipeline_total > 0 else None,
            )
        except asyncio.TimeoutError:
            cancel.set()
            raise ToolError({
                "error": f"Pipeline timed out after {tc.pipeline_total:.0f}s",
                "error_code": "PIPELINE_TIMEOUT",
                "status": "timeout",
                "timeout_seconds": tc.pipeline_total,
            })
        return result

    def _run_pipeline(
        self,
        repo_path: Path,
        rebuild: bool,
        wiki_mode: str,
        progress_cb: ProgressCb = None,
        backend: str = "kuzu",
        skip_wiki: bool = False,
        skip_embed: bool = False,
        cancel_event: threading.Event | None = None,
        timeout_cfg: "TimeoutConfig | None" = None,
    ) -> dict[str, Any]:
        """Synchronous pipeline orchestrator: graph -> api_docs -> embeddings.

        Wiki generation is not part of the main pipeline -- use the
        ``generate_wiki`` tool separately if needed.
        """
        from terrain.foundation.types.config import TimeoutConfig
        tc = timeout_cfg or TimeoutConfig()
        cancel = cancel_event or threading.Event()

        def _check_cancel(step_name: str) -> None:
            """Raise if the pipeline has been cancelled (overall timeout)."""
            if cancel.is_set():
                raise _PipelineTimeout(step_name, 0)

        def _step_with_timeout(
            step_name: str, timeout: float, fn, *args, **kwargs,
        ):
            """Run *fn* and raise ``_PipelineTimeout`` if it exceeds *timeout*."""
            _check_cancel(step_name)
            t0 = time.monotonic()
            result = fn(*args, **kwargs)
            elapsed = time.monotonic() - t0
            if timeout > 0 and elapsed > timeout:
                raise _PipelineTimeout(step_name, elapsed)
            _check_cancel(step_name)
            return result

        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"

        total_steps = 2 if skip_embed else 3

        def _step_progress(step: int, total: int, msg: str, pct: float) -> None:
            if progress_cb:
                progress_cb(f"[Step {step}/{total}] {msg}", pct)

        try:
            # Close existing MCP connection first so the builder can
            # open the database without lock contention.
            self.close()

            # Step 1: build graph (read-write)
            builder = _step_with_timeout(
                "graph_build", tc.graph_build,
                build_graph,
                repo_path, db_path, rebuild,
                progress_cb=lambda msg, pct: _step_progress(1, total_steps, msg, pct),
                backend=backend,
            )
            # IMPORTANT: On Windows, Kuzu holds mandatory file locks via
            # the C++ Database object.  We must delete all references and
            # force GC before opening a new connection.
            import gc
            if hasattr(builder, '_ingestor'):
                builder._ingestor = None
            del builder
            gc.collect()

            # Step 2: generate API docs (needs read-only Kuzu access)
            ro_ingestor = KuzuIngestor(db_path, read_only=True)
            with ro_ingestor:
                _step_with_timeout(
                    "api_docs", tc.api_docs,
                    generate_api_docs_step,
                    ro_ingestor, artifact_dir, rebuild,
                    progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
                    repo_path=repo_path,
                )

                # Validate API docs -- retry with rebuild if incomplete
                validation = validate_api_docs(artifact_dir)
                if not validation["valid"]:
                    logger.warning(
                        f"API docs validation failed: {validation['issues']}. Retrying..."
                    )
                    _step_progress(
                        2, total_steps,
                        f"API docs incomplete ({', '.join(validation['issues'])}), retrying...",
                        12.0,
                    )
                    _step_with_timeout(
                        "api_docs_retry", tc.api_docs,
                        generate_api_docs_step,
                        ro_ingestor, artifact_dir, True,
                        progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
                        repo_path=repo_path,
                    )
                    validation = validate_api_docs(artifact_dir)
                    if validation["valid"]:
                        logger.info(
                            f"API docs retry succeeded: "
                            f"{validation['modules']} modules, "
                            f"{validation['funcs']} functions"
                        )
                    else:
                        logger.warning(
                            f"API docs retry still incomplete: {validation['issues']}"
                        )
            # Kuzu no longer needed after this point

            # Step 2b: LLM description generation for undocumented functions
            _step_with_timeout(
                "descriptions", tc.descriptions,
                generate_descriptions_step,
                artifact_dir=artifact_dir,
                repo_path=repo_path,
                progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
            )

            skipped = []

            if not skip_embed:
                # Step 3: build embeddings from API doc Markdown files (no Kuzu)
                _step_with_timeout(
                    "embeddings", tc.embeddings,
                    build_vector_index,
                    None, repo_path, vectors_path, rebuild,
                    progress_cb=lambda msg, pct: _step_progress(3, total_steps, msg, pct),
                )
            else:
                skipped.append("embed")
                _step_progress(total_steps, total_steps, "Embedding skipped.", 100.0)

            #
            final_validation = validate_api_docs(artifact_dir)
            _head = _GCD().get_current_head(repo_path)
            save_meta(artifact_dir, repo_path, 0, last_indexed_commit=_head)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            result: dict[str, Any] = {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "skipped": skipped,
                "api_docs": final_validation,
            }
            if not final_validation["valid"]:
                result["warnings"] = [
                    f"API docs incomplete: {', '.join(final_validation['issues'])}"
                ]

            # Guide the agent on what to do next
            modules = final_validation.get("modules", 0)
            funcs = final_validation.get("funcs", 0)
            result["next_steps"] = (
                f"Repository indexed successfully: {modules} modules, {funcs} functions documented.\n"
                "You now have full code intelligence for this repo. Here is what you can do:\n"
                "- `find_api` -- search by natural language (e.g. 'how does logging work?')\n"
                "- `list_api_docs` -- browse all modules and their functions\n"
                "- `get_api_doc` -- read detailed docs for any function (signature, call tree, source)\n"
                "- `find_callers` -- find every function that calls a given function\n"
                "- `trace_call_chain` -- trace the full call chain from entry points to a target\n"
                "- `find_symbol_in_docs` -- find symbol usage: doc-based global/constant refs by default, "
                "or pass `mode`/`qualified_scope` for AST-level read/write sites of a C/C++ variable\n\n"
                "Tell the user what was indexed and ask what they would like to explore."
            )
            return result

        except _PipelineTimeout as toe:
            logger.warning(f"Pipeline step timed out: {toe}")
            raise ToolError({
                "error": str(toe),
                "error_code": "STEP_TIMEOUT",
                "status": "timeout",
                "step": toe.step_name,
                "elapsed_seconds": toe.elapsed,
            }) from toe
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("Pipeline failed")
            raise ToolError({"error": str(exc), "error_code": "PIPELINE_ERROR", "status": "error"}) from exc


    # -------------------------------------------------------------------------
    # get_repository_info (merged: active repo metadata + graph statistics)
    # -------------------------------------------------------------------------

    async def _handle_get_repository_info(self) -> dict[str, Any]:
        if self._active_artifact_dir is None:
            raise ToolError("No active repository. Run `terrain index <path>` first.")

        meta_file = self._active_artifact_dir / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace")) if meta_file.exists() else {}

        wiki_pages = []
        wiki_subdir = self._active_artifact_dir / "wiki" / "wiki"
        if wiki_subdir.exists():
            wiki_pages = [p.stem for p in sorted(wiki_subdir.glob("*.md"))]

        warnings: list[str] = []
        if self._semantic_service is None:
            warnings.append(
                "Semantic search unavailable — check embedding API keys "
                "(DASHSCOPE_API_KEY or EMBEDDING_API_KEY/OPENAI_API_KEY)."
            )
        if self._cypher_gen is None:
            warnings.append(
                "Cypher query unavailable — set LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY to enable natural language queries."
            )

        result: dict[str, Any] = {
            "repo_path": str(self._active_repo_path),
            "artifact_dir": str(self._active_artifact_dir),
            "indexed_at": meta.get("indexed_at"),
            "semantic_search_available": self._semantic_service is not None,
            "cypher_query_available": self._cypher_gen is not None,
            "wiki_pages": wiki_pages,
        }
        if warnings:
            result["warnings"] = warnings

        # Merge graph statistics + language stats using temporary connection
        try:
            with self._temporary_ingestor() as ingestor:
                result["graph_stats"] = ingestor.get_statistics()

                # Language extraction stats
                try:
                    file_rows = ingestor.query(
                        "MATCH (f:File) RETURN f.path AS path"
                    )
                    from terrain.foundation.parsers.language_spec import get_language_for_extension
                    lang_counts: dict[str, int] = {}
                    total_files = 0
                    for row in file_rows:
                        raw = row.get("result", row)
                        fpath = raw[0] if isinstance(raw, (list, tuple)) else raw
                        if isinstance(fpath, str):
                            ext = Path(fpath).suffix.lower()
                            lang = get_language_for_extension(ext)
                            if lang:
                                lang_counts[lang.value] = lang_counts.get(lang.value, 0) + 1
                                total_files += 1
                    result["language_stats"] = {
                        "total_code_files": total_files,
                        "by_language": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),
                    }
                except Exception:
                    pass  # language stats are optional
        except Exception as exc:
            result["graph_stats"] = {"error": str(exc)}

        # Supported languages
        from terrain.foundation.types.constants import LANGUAGE_METADATA, LanguageStatus
        result["supported_languages"] = {
            "full": [m.display_name for _, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.FULL],
            "in_development": [m.display_name for _, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.DEV],
        }

        return result

    # -------------------------------------------------------------------------
    # list_repositories / switch_repository
    # -------------------------------------------------------------------------

    async def _handle_list_repositories(self) -> dict[str, Any]:
        # Lazy import avoids a cli↔tools circular import at module load time.
        from terrain.entrypoints.cli.cli import _get_repo_status_entries

        active_name = None
        active_file = self._workspace / "active.txt"
        if active_file.exists():
            active_name = active_file.read_text(encoding="utf-8", errors="replace").strip()

        try:
            status_entries = _get_repo_status_entries(self._workspace)
        except Exception as exc:  # pragma: no cover — defensive: never 500 the MCP tool
            logger.debug("staleness lookup failed: {}", exc)
            status_entries = []
        status_by_artifact = {e["artifact_dir"]: e for e in status_entries}

        repos: list[dict[str, Any]] = []
        for child in sorted(self._workspace.iterdir()):
            if not child.is_dir():
                continue
            meta_file = child / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue

            steps = meta.get("steps", {})
            status_entry = status_by_artifact.get(child.name)
            # Half-built repo (no graph yet) — staleness is meaningless.
            if not steps.get("graph") or status_entry is None:
                staleness = "unknown"
                indexed_head = None
                current_head = None
                commits_since = None
            else:
                staleness = status_entry["status"]
                indexed_head = status_entry["indexed_head"]
                current_head = status_entry["current_head"]
                commits_since = status_entry["commits_since"]

            repos.append({
                "artifact_dir": child.name,
                "repo_name": meta.get("repo_name", child.name),
                "repo_path": meta.get("repo_path", "unknown"),
                "indexed_at": meta.get("indexed_at"),
                "wiki_page_count": meta.get("wiki_page_count", 0),
                "steps": steps,
                "active": child.name == active_name,
                "staleness": staleness,
                "indexed_head": indexed_head,
                "current_head": current_head,
                "commits_since": commits_since,
            })

        return {
            "workspace": str(self._workspace),
            "repository_count": len(repos),
            "repositories": repos,
            "hint": (
                "Use switch_repository with repo_name to change the active repo. "
                "Use `terrain index <path>` or build_graph to index a new repo. "
                "Check `staleness` before trusting graph results: "
                "'stale' means HEAD has moved since indexing — consider re-indexing."
            ),
        }

    async def _handle_switch_repository(self, repo_name: str) -> dict[str, Any]:
        # Try exact match on artifact_dir name first
        target: Path | None = None
        for child in self._workspace.iterdir():
            if not child.is_dir():
                continue
            if child.name == repo_name:
                target = child
                break

        # Fallback: match by repo_name in meta.json
        if target is None:
            for child in sorted(self._workspace.iterdir()):
                if not child.is_dir():
                    continue
                meta_file = child / "meta.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    continue
                if meta.get("repo_name") == repo_name:
                    target = child
                    break

        if target is None or not (target / "meta.json").exists():
            raise ToolError({
                "error": f"Repository not found: {repo_name}",
                "hint": "Use list_repositories to see available repos.",
            })

        try:
            self._set_active(target)
            self._load_services(target)
        except Exception as exc:
            raise ToolError({
                "error": f"Failed to switch: {exc}",
                "repo_name": repo_name,
            }) from exc

        meta = json.loads((target / "meta.json").read_text(encoding="utf-8", errors="replace"))
        return {
            "status": "success",
            "active_repo": meta.get("repo_name", target.name),
            "repo_path": meta.get("repo_path"),
            "artifact_dir": str(target),
            "steps": meta.get("steps", {}),
        }

    # -------------------------------------------------------------------------
    # link_repository — symlink a new repo path to an existing index
    # -------------------------------------------------------------------------

    async def _handle_link_repository(
        self,
        repo_path: str,
        source_repo: str,
    ) -> dict[str, Any]:
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise ToolError(f"repo_path does not exist: {repo_path}")

        # Find source artifact dir (same logic as switch_repository)
        source_dir: Path | None = None
        for child in self._workspace.iterdir():
            if not child.is_dir():
                continue
            if child.name == source_repo:
                source_dir = child
                break
        if source_dir is None:
            for child in sorted(self._workspace.iterdir()):
                if not child.is_dir():
                    continue
                meta_file = child / "meta.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    continue
                if meta.get("repo_name") == source_repo:
                    source_dir = child
                    break

        if source_dir is None or not (source_dir / "meta.json").exists():
            raise ToolError({
                "error": f"Source repository not found: {source_repo}",
                "hint": "Use list_repositories to see available repos.",
            })

        # Create new artifact dir for this repo_path
        new_dir = artifact_dir_for(self._workspace, repo)
        if new_dir == source_dir:
            raise ToolError("repo_path resolves to the same artifact as source_repo.")

        if new_dir.exists():
            raise ToolError({
                "error": f"Artifact directory already exists: {new_dir.name}",
                "hint": "Use switch_repository to activate it, or delete it first.",
            })

        new_dir.mkdir(parents=True)

        # Symlink all data artifacts from source
        artifacts = ["graph.db", "api_docs", "vectors.pkl", "wiki"]
        linked = []
        for name in artifacts:
            src = source_dir / name
            if src.exists():
                dst = new_dir / name
                dst.symlink_to(src)
                linked.append(name)

        # JER-101: unified link writer — stamps schema v2 on the target and
        # upserts linked_repos on the authoritative source meta.
        from terrain.entrypoints.link_ops import register_link
        from terrain.foundation.utils.paths import normalize_repo_path

        register_link(
            self._workspace,
            source_dir=source_dir,
            target_dir=new_dir,
            repo_path=repo,
        )
        # Preserve the legacy ``linked_to`` / ``linked_source_repo`` keys that
        # existing MCP clients still key off. register_link already wrote
        # ``linked_from``; add back the MCP-specific aliases.
        meta_file = new_dir / "meta.json"
        new_meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        source_meta = json.loads(
            (source_dir / "meta.json").read_text(encoding="utf-8", errors="replace")
        )
        new_meta["linked_to"] = str(source_dir)
        new_meta["linked_source_repo"] = source_meta.get("repo_name", source_dir.name)
        meta_file.write_text(
            json.dumps(new_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Activate the new linked repo
        self._set_active(new_dir)
        self._load_services(new_dir)

        return {
            "status": "success",
            "repo_path": normalize_repo_path(repo),
            "artifact_dir": str(new_dir),
            "linked_to": source_dir.name,
            "linked_artifacts": linked,
            "message": (
                f"Linked {repo.name} → {source_dir.name}. "
                f"Shared artifacts: {', '.join(linked)}. "
                f"Now active."
            ),
        }

    # -------------------------------------------------------------------------
    # query_code_graph
    # -------------------------------------------------------------------------

    async def _handle_query_code_graph(self, question: str) -> dict[str, Any]:
        self._require_active()

        if self._cypher_gen is None:
            raise ToolError(
                "LLM not configured. Set one of: LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY in the MCP server environment."
            )

        try:
            cypher = self._cypher_gen.generate(question)
        except Exception as exc:
            raise ToolError({"error": f"Cypher generation failed: {exc}", "question": question}) from exc

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.query(cypher)
                serialisable = []
                for row in rows:
                    raw = row.get("result", row)
                    if isinstance(raw, (list, tuple)):
                        serialisable.append(list(raw))
                    else:
                        serialisable.append(raw)
                return {
                    "question": question,
                    "cypher": cypher,
                    "row_count": len(serialisable),
                    "rows": serialisable,
                }
        except Exception as exc:
            raise ToolError({
                "error": f"Query execution failed: {exc}",
                "question": question,
                "cypher": cypher,
            }) from exc

    # -------------------------------------------------------------------------
    # get_code_snippet
    # -------------------------------------------------------------------------

    async def _handle_get_code_snippet(self, qualified_name: str) -> dict[str, Any]:
        self._require_active()

        safe_qn = qualified_name.replace("'", "\\'")
        cypher = (
            f"MATCH (n) WHERE n.qualified_name = '{safe_qn}' "
            "RETURN n.qualified_name, n.name, n.source_code, n.path, n.start_line, n.end_line "
            "LIMIT 1"
        )

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.query(cypher)
        except Exception as exc:
            raise ToolError({"error": f"Graph query failed: {exc}", "qualified_name": qualified_name}) from exc

        if not rows:
            raise ToolError({"error": "Not found", "qualified_name": qualified_name})

        result = rows[0].get("result", [])
        qname = result[0] if len(result) > 0 else qualified_name
        name = result[1] if len(result) > 1 else None
        source_code = result[2] if len(result) > 2 else None
        file_path = result[3] if len(result) > 3 else None
        start_line = result[4] if len(result) > 4 else None
        end_line = result[5] if len(result) > 5 else None

        if not source_code and file_path and start_line and end_line:
            fp = Path(str(file_path))
            if not fp.is_absolute() and self._active_repo_path:
                fp = self._active_repo_path / fp
            try:
                from terrain.foundation.utils.encoding import read_source_file
                lines = read_source_file(fp).splitlines(keepends=True)
                s = max(0, int(start_line) - 1)
                e = min(len(lines), int(end_line))
                source_code = "".join(lines[s:e])
            except Exception:
                pass

        return {
            "qualified_name": qname,
            "name": name,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "source_code": source_code,
        }

    # -------------------------------------------------------------------------
    # semantic_search
    # -------------------------------------------------------------------------

    async def _handle_semantic_search(
        self,
        query: str,
        top_k: int = 5,
        entity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._semantic_service is None:
            raise ToolError("Semantic search not available. Run `terrain index <path>` to build embeddings.")

        try:
            results = self._semantic_service.search(query, top_k=top_k, entity_types=entity_types)
            return {
                "query": query,
                "result_count": len(results),
                "results": [
                    {
                        "qualified_name": r.qualified_name,
                        "name": r.name,
                        "type": r.type,
                        "score": r.score,
                        "file_path": r.file_path,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "source_code": r.source_code,
                    }
                    for r in results
                ],
            }
        except Exception as exc:
            raise ToolError({"error": f"Semantic search failed: {exc}", "query": query}) from exc

    # -------------------------------------------------------------------------
    # path safety helper (used by locate_function)
    # -------------------------------------------------------------------------

    def _safe_path(self, rel_path: str) -> Path | None:
        if self._active_repo_path is None:
            return None
        target = (self._active_repo_path / rel_path).resolve()
        try:
            target.relative_to(self._active_repo_path.resolve())
        except ValueError:
            return None
        return target

    # -------------------------------------------------------------------------
    # wiki tools
    # -------------------------------------------------------------------------

    def _wiki_dir(self) -> Path | None:
        if self._active_artifact_dir is None:
            return None
        return self._active_artifact_dir / "wiki"

    async def _handle_list_wiki_pages(self) -> dict[str, Any]:
        self._require_active()

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            raise ToolError("Wiki not generated yet. Run `terrain index <path>` first.")

        pages = []
        wiki_subdir = wiki_dir / "wiki"
        if wiki_subdir.exists():
            for p in sorted(wiki_subdir.glob("*.md")):
                pages.append({"page_id": p.stem, "file": f"wiki/{p.name}"})

        index_path = wiki_dir / "index.md"
        return {
            "index_available": index_path.exists(),
            "page_count": len(pages),
            "pages": pages,
            "hint": "Use get_wiki_page with page_id='index' for the summary, or a specific page-N id.",
        }

    async def _handle_get_wiki_page(self, page_id: str) -> dict[str, Any]:
        self._require_active()

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            raise ToolError("Wiki not generated yet. Run `terrain index <path>` first.")

        if page_id == "index":
            target = wiki_dir / "index.md"
        else:
            target = wiki_dir / "wiki" / f"{page_id}.md"

        if not target.exists():
            raise ToolError({"error": f"Wiki page not found: {page_id}", "page_id": page_id})

        content = target.read_text(encoding="utf-8", errors="ignore")
        return {
            "page_id": page_id,
            "file_path": str(target),
            "content": content,
        }

    # -------------------------------------------------------------------------
    # locate_function
    # -------------------------------------------------------------------------

    async def _handle_locate_function(
        self,
        file_path: str,
        function_name: str,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        self._require_repo_path()
        if self._file_editor is None:
            raise ToolError("File editor not initialized.")

        target = self._safe_path(file_path)
        if target is None:
            raise ToolError({"error": "Path outside repository root.", "file_path": file_path})
        if not target.exists():
            raise ToolError({"error": "File not found.", "file_path": file_path})

        result = self._file_editor.locate_function(target, function_name, line_number)
        if result is None:
            raise ToolError({
                "error": f"Function '{function_name}' not found in {file_path}.",
                "file_path": file_path,
                "function_name": function_name,
            })
        return result

    # -------------------------------------------------------------------------
    # list_api_interfaces
    # -------------------------------------------------------------------------

    async def _handle_list_api_interfaces(
        self,
        module: str | None = None,
        visibility: str = "public",
        include_types: bool = True,
    ) -> dict[str, Any]:
        self._require_active()

        vis_filter = None if visibility == "all" else visibility

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.fetch_module_apis(
                    module_qn=module,
                    visibility=vis_filter,
                )

            # Group function results by module
            by_module: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                raw = row.get("result", row)
                if isinstance(raw, (list, tuple)) and len(raw) >= 8:
                    mod_name = raw[0] or "unknown"
                    entry: dict[str, Any] = {
                        "name": raw[1],
                        "signature": raw[2],
                        "return_type": raw[3],
                        "visibility": raw[4],
                        "parameters": raw[5],
                        "start_line": raw[6],
                        "end_line": raw[7],
                        "entity_type": "function",
                    }
                else:
                    mod_name = raw.get("module", "unknown") if isinstance(raw, dict) else "unknown"
                    entry = raw if isinstance(raw, dict) else {"raw": raw}
                    if isinstance(entry, dict):
                        entry["entity_type"] = "function"

                if mod_name not in by_module:
                    by_module[mod_name] = []
                by_module[mod_name].append(entry)

            # Fetch type APIs (structs, unions, enums, typedefs) if requested
            type_count = 0
            if include_types and hasattr(ingestor, "fetch_module_type_apis"):
                type_rows = ingestor.fetch_module_type_apis(module_qn=module)
                for row in type_rows:
                    raw = row.get("result", row)
                    if isinstance(raw, (list, tuple)) and len(raw) >= 6:
                        mod_name = raw[0] or "unknown"
                        entry = {
                            "name": raw[1],
                            "kind": raw[2],
                            "signature": raw[3],
                            "members": raw[4] if len(raw) > 4 else None,
                            "start_line": raw[4 if len(raw) <= 5 else 5],
                            "end_line": raw[5 if len(raw) <= 6 else 6],
                            "entity_type": raw[2] or "type",
                        }
                    else:
                        mod_name = raw.get("module", "unknown") if isinstance(raw, dict) else "unknown"
                        entry = raw if isinstance(raw, dict) else {"raw": raw}

                    if mod_name not in by_module:
                        by_module[mod_name] = []
                    by_module[mod_name].append(entry)
                    type_count += 1

            total = sum(len(v) for v in by_module.values())
            return {
                "total_apis": total,
                "function_count": total - type_count,
                "type_count": type_count,
                "module_count": len(by_module),
                "visibility_filter": visibility,
                "modules": by_module,
            }

        except Exception as exc:
            raise ToolError(f"Failed to list API interfaces: {exc}") from exc

    # -------------------------------------------------------------------------
    # list_api_docs / get_api_doc  (hierarchical API documentation)
    # -------------------------------------------------------------------------

    def _api_docs_dir(self) -> Path | None:
        if self._active_artifact_dir is None:
            return None
        return self._active_artifact_dir / "api_docs"

    async def _handle_list_api_docs(
        self,
        module: str | None = None,
    ) -> dict[str, Any]:
        self._require_active()

        api_dir = self._api_docs_dir()
        if api_dir is None or not (api_dir / "index.md").exists():
            raise ToolError(
                "API docs not generated yet. "
                "Run `terrain index <path>` to generate them."
            )

        if module:
            # L2: module detail page
            safe = module.replace("/", "_").replace("\\", "_")
            target = api_dir / "modules" / f"{safe}.md"
            if not target.exists():
                raise ToolError({
                    "error": f"Module doc not found: {module}",
                    "module": module,
                    "hint": "Use list_api_docs (no args) to see available modules.",
                })
            return {
                "level": "module",
                "module": module,
                "content": target.read_text(encoding="utf-8", errors="ignore"),
            }

        # L1: global index
        index_path = api_dir / "index.md"
        return {
            "level": "index",
            "content": index_path.read_text(encoding="utf-8", errors="ignore"),
        }

    async def _handle_get_api_doc(
        self,
        qualified_name: str,
    ) -> dict[str, Any]:
        self._require_active()

        api_dir = self._api_docs_dir()
        if api_dir is None or not (api_dir / "index.md").exists():
            raise ToolError(
                "API docs not generated yet. "
                "Run `terrain index <path>` to generate them."
            )

        safe = qualified_name.replace("/", "_").replace("\\", "_")
        target = api_dir / "funcs" / f"{safe}.md"
        if not target.exists():
            raise ToolError({
                "error": f"API doc not found: {qualified_name}",
                "qualified_name": qualified_name,
                "hint": "Use list_api_docs to browse modules first.",
            })

        content = target.read_text(encoding="utf-8", errors="ignore")

        # Live caller query — overrides potentially stale caller section in the MD.
        # Also collect callees_count so agents can decide whether to deep-read
        # without a second tool call (JER-70).
        live_callers: list[dict] = []
        callees_count: int | None = None
        try:
            callers_cypher = """
                MATCH (caller)-[:CALLS]->(callee)
                WHERE callee.qualified_name = $qn OR callee.name = $name
                RETURN DISTINCT
                       caller.qualified_name AS caller_qn,
                       caller.name           AS caller_name,
                       caller.path           AS caller_path,
                       caller.start_line     AS caller_start
            """
            callees_cypher = """
                MATCH (f:Function {qualified_name: $qn})-[:CALLS]->(c)
                RETURN count(DISTINCT c) AS n
            """
            simple_name = qualified_name.split(".")[-1]
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.query(
                    callers_cypher, {"qn": qualified_name, "name": simple_name}
                )
                try:
                    crows = ingestor.query(callees_cypher, {"qn": qualified_name})
                    if crows:
                        callees_count = int(crows[0].get("n", 0) or 0)
                except Exception:
                    callees_count = None
            seen: set[str] = set()
            for r in rows:
                key = r.get("caller_qn") or r.get("caller_name", "")
                if key and key not in seen:
                    seen.add(key)
                    live_callers.append({
                        "qualified_name": r.get("caller_qn", ""),
                        "name": r.get("caller_name", ""),
                        "path": r.get("caller_path", ""),
                        "start_line": r.get("caller_start"),
                    })
        except Exception:
            pass  # live callers are supplemental; don't fail the whole call

        response: dict[str, Any] = {
            "qualified_name": qualified_name,
            "content": content,
            "live_callers": live_callers,
        }
        if callees_count is not None:
            response["callees_count"] = callees_count
            response["is_leaf"] = callees_count == 0
        return response

    # -------------------------------------------------------------------------
    # find_api  (aggregated: semantic search + API doc lookup)
    # -------------------------------------------------------------------------

    async def _handle_find_api(
        self,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        self._require_active()

        api_dir = self._api_docs_dir()
        funcs_dir = api_dir / "funcs" if api_dir else None
        has_api_docs = funcs_dir is not None and funcs_dir.exists()

        if self._semantic_service is None:
            # Fallback: keyword search over API doc filenames + content headers
            if not has_api_docs:
                raise ToolError(
                    "Semantic search unavailable and no API docs found. "
                    "Run `terrain index <path>` first."
                )
            keywords = [w.lower() for w in query.split() if len(w) > 2]
            scored: list[tuple[float, Path]] = []
            for md_file in funcs_dir.glob("*.md"):
                name_lower = md_file.stem.lower()
                score = sum(1.0 for kw in keywords if kw in name_lower)
                if score == 0:
                    try:
                        head = md_file.read_text(encoding="utf-8", errors="ignore")[:400]
                        head_lower = head.lower()
                        score = sum(0.5 for kw in keywords if kw in head_lower)
                    except Exception:
                        pass
                if score > 0:
                    scored.append((score, md_file))
            scored.sort(key=lambda x: -x[0])
            combined = []
            for _, md_file in scored[:top_k]:
                qn = md_file.stem
                full_doc = md_file.read_text(encoding="utf-8", errors="ignore")
                combined.append({
                    "qualified_name": qn,
                    "name": qn.split(".")[-1],
                    "score": None,
                    "api_doc": summarize_api_doc(full_doc),
                })
            return {
                "query": query,
                "result_count": len(combined),
                "search_mode": "keyword_fallback",
                "api_docs_available": True,
                "results": combined,
            }

        try:
            results = self._semantic_service.search(query, top_k=top_k)
        except Exception as exc:
            raise ToolError(
                {"error": f"Semantic search failed: {exc}", "query": query}
            ) from exc

        combined = []
        for r in results:
            entry: dict[str, Any] = {
                "qualified_name": r.qualified_name,
                "name": r.name,
                "type": r.type,
                "score": r.score,
                "file_path": r.file_path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "api_doc": None,
            }

            if has_api_docs and r.qualified_name:
                safe_qn = r.qualified_name.replace("/", "_").replace("\\", "_")
                doc_file = funcs_dir / f"{safe_qn}.md"
                if doc_file.exists():
                    full_doc = doc_file.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                    entry["api_doc"] = summarize_api_doc(full_doc)

            combined.append(entry)

        # Batch callees count so agents see leafness without a second round-trip
        # (JER-70). One cypher round-trip regardless of result count.
        qns_for_leaf = [
            e["qualified_name"] for e in combined
            if e.get("qualified_name") and e.get("type") == "function"
        ]
        if qns_for_leaf:
            try:
                cypher = """
                    MATCH (f:Function) WHERE f.qualified_name IN $qns
                    OPTIONAL MATCH (f)-[:CALLS]->(c)
                    RETURN f.qualified_name AS qn, count(DISTINCT c) AS n
                """
                with self._temporary_ingestor() as ingestor:
                    rows = ingestor.query(cypher, {"qns": qns_for_leaf})
                counts: dict[str, int] = {}
                for row in rows:
                    qn = row.get("qn")
                    if qn is not None:
                        counts[qn] = int(row.get("n", 0) or 0)
                for entry in combined:
                    qn = entry.get("qualified_name")
                    if qn in counts:
                        entry["callees_count"] = counts[qn]
                        entry["is_leaf"] = counts[qn] == 0
            except Exception:
                pass  # leafness is supplemental; don't fail the whole call

        return {
            "query": query,
            "result_count": len(combined),
            "api_docs_available": has_api_docs,
            "results": combined,
        }

    # -------------------------------------------------------------------------
    # generate_wiki  (standalone wiki regeneration)
    # -------------------------------------------------------------------------

    async def _handle_generate_wiki(
        self,
        wiki_mode: str = "comprehensive",
        rebuild: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Run `terrain index <path>` first.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path
        vectors_path = artifact_dir / "vectors.pkl"

        if not vectors_path.exists():
            raise ToolError(
                "Embeddings not found. Run `terrain index <path>` first"
                "to build the graph and embeddings."
            )

        loop = asyncio.get_running_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_wiki_generation(
                repo_path, artifact_dir, vectors_path,
                wiki_mode, rebuild, sync_progress,
            ),
        )
        return result

    def _run_wiki_generation(
        self,
        repo_path: Path,
        artifact_dir: Path,
        vectors_path: Path,
        wiki_mode: str,
        rebuild: bool,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous wiki generation using existing graph + embeddings."""
        from terrain.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE

        comprehensive = wiki_mode != "concise"
        max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE
        wiki_dir = artifact_dir / "wiki"

        try:
            # Load existing embeddings
            with open(vectors_path, "rb") as fh:
                cache = pickle.load(fh)
            vector_store = cache["vector_store"]
            func_map: dict[int, dict] = cache["func_map"]
            from terrain.domains.core.embedding.qwen3_embedder import create_embedder
            embedder = create_embedder()

            # Delete structure cache if rebuild
            structure_cache = wiki_dir / f"{repo_path.name}_structure.pkl"
            if rebuild and structure_cache.exists():
                structure_cache.unlink()

            with self._temporary_ingestor() as ingestor:
                index_path, page_count = run_wiki_generation(
                    builder=ingestor,
                    repo_path=repo_path,
                    output_dir=wiki_dir,
                    max_pages=max_pages,
                    rebuild=rebuild,
                    comprehensive=comprehensive,
                    vector_store=vector_store,
                    embedder=embedder,
                    func_map=func_map,
                    progress_cb=progress_cb,
                )

            _head = _GCD().get_current_head(repo_path)
            save_meta(artifact_dir, repo_path, page_count, last_indexed_commit=_head)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "wiki_index": str(index_path),
                "wiki_pages": page_count,
            }

        except Exception as exc:
            logger.exception("Wiki generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # rebuild_embeddings  (standalone embedding rebuild)
    # -------------------------------------------------------------------------

    async def _handle_rebuild_embeddings(
        self,
        rebuild: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Run `terrain index <path>` first.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path
        db_path = artifact_dir / "graph.db"

        if not db_path.exists():
            raise ToolError(
                "Graph database not found. Run `terrain index <path>` first"
                "to build the knowledge graph."
            )

        loop = asyncio.get_running_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_rebuild_embeddings(
                repo_path, artifact_dir, rebuild, sync_progress,
            ),
        )

        # Reload services so semantic search picks up new embeddings
        self._load_services(artifact_dir)

        return result

    def _run_rebuild_embeddings(
        self,
        repo_path: Path,
        artifact_dir: Path,
        rebuild: bool,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous embedding rebuild using existing graph."""
        vectors_path = artifact_dir / "vectors.pkl"

        try:
            with self._temporary_ingestor() as ingestor:
                vector_store, embedder, func_map = build_vector_index(
                    ingestor, repo_path, vectors_path, rebuild, progress_cb
                )

            # Preserve existing wiki_page_count in meta
            meta_file = artifact_dir / "meta.json"
            page_count = 0
            if meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                page_count = meta.get("wiki_page_count", 0)

            _head = _GCD().get_current_head(repo_path)
            save_meta(artifact_dir, repo_path, page_count, last_indexed_commit=_head)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "vectors_path": str(vectors_path),
                "embedding_count": len(vector_store),
            }

        except Exception as exc:
            logger.exception("Embedding rebuild failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # build_graph  (standalone graph build)
    # -------------------------------------------------------------------------

    async def _handle_build_graph(
        self,
        repo_path: str,
        rebuild: bool = False,
        backend: str = "kuzu",
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise ToolError(f"Repository path does not exist: {repo}")

        loop = asyncio.get_running_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_build_graph(repo, rebuild, backend, sync_progress),
        )
        return result

    def _run_build_graph(
        self,
        repo_path: Path,
        rebuild: bool,
        backend: str,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous graph build. Runs in thread pool."""
        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        db_path = artifact_dir / "graph.db"

        try:
            # Close existing MCP connection first so the builder can
            # open the database without lock contention.
            self.close()

            builder = build_graph(
                repo_path, db_path, rebuild, progress_cb, backend=backend,
            )

            # Release builder references so Windows file locks are freed
            import gc
            if hasattr(builder, '_ingestor'):
                builder._ingestor = None
            del builder
            gc.collect()

            # Build is done (write connection closed). Open read-only
            # to get statistics, then release for _load_services.
            ro_ingestor = KuzuIngestor(db_path, read_only=True)
            with ro_ingestor:
                stats = ro_ingestor.get_statistics()

            _head = _GCD().get_current_head(repo_path)
            save_meta(artifact_dir, repo_path, 0, last_indexed_commit=_head)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "node_count": stats.get("node_count", 0),
                "relationship_count": stats.get("relationship_count", 0),
            }

        except Exception as exc:
            logger.exception("Graph build failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # generate_api_docs  (standalone API doc generation)
    # -------------------------------------------------------------------------

    async def _handle_generate_api_docs(
        self,
        mode: str = "full",
        _progress_cb: ProgressCb = None,
        # Legacy param kept for backward compatibility
        rebuild: bool = False,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Run `terrain index <path>` first.")

        if mode not in ("full", "resume", "enhance"):
            raise ToolError(f"Invalid mode '{mode}'. Must be 'full', 'resume', or 'enhance'.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path

        loop = asyncio.get_running_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_generate_api_docs(artifact_dir, repo_path, mode, sync_progress),
        )
        return result

    def _run_generate_api_docs(
        self,
        artifact_dir: Path,
        repo_path: Path | None,
        mode: str = "full",
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous API docs generation from existing graph.

        Args:
            mode: 'full' = regenerate docs from graph + LLM descriptions.
                  'resume' = only run LLM descriptions for remaining TODOs.
        """
        try:
            if mode == "full":
                with self._temporary_ingestor() as ingestor:
                    result = generate_api_docs_step(
                        ingestor, artifact_dir, True, progress_cb,
                        repo_path=repo_path,
                    )

                    # Validate and retry if incomplete
                    validation = validate_api_docs(artifact_dir)
                    if not validation["valid"] and result.get("status") != "cached":
                        logger.warning(
                            f"API docs validation failed: {validation['issues']}. Retrying..."
                        )
                        result = generate_api_docs_step(
                            ingestor, artifact_dir, rebuild=True, progress_cb=progress_cb,
                            repo_path=repo_path,
                        )
                        validation = validate_api_docs(artifact_dir)

                    result["validation"] = validation

                # LLM description generation for undocumented functions
                if repo_path is not None:
                    desc_stats = generate_descriptions_step(
                        artifact_dir=artifact_dir,
                        repo_path=repo_path,
                        progress_cb=progress_cb,
                    )
                    result["desc_stats"] = desc_stats

                return {
                    "status": result.get("status", "success"),
                    "artifact_dir": str(artifact_dir),
                    **{k: v for k, v in result.items() if k != "status"},
                }

            elif mode == "resume":
                funcs_dir = artifact_dir / "api_docs" / "funcs"
                if not funcs_dir.exists():
                    raise ToolError(
                        "No API docs found. Run with mode='full' first."
                    )

                todo_funcs = _collect_todo_funcs(funcs_dir)
                total_todo = len(todo_funcs)

                if total_todo == 0:
                    return {
                        "status": "success",
                        "message": "All functions already have LLM descriptions.",
                        "remaining_todo": 0,
                    }

                if progress_cb:
                    progress_cb(
                        f"Resuming LLM description generation: {total_todo} functions remaining",
                        0.0,
                    )

                desc_stats = generate_descriptions_step(
                    artifact_dir=artifact_dir,
                    repo_path=repo_path,
                    progress_cb=progress_cb,
                )

                remaining = len(_collect_todo_funcs(funcs_dir))

                return {
                    "status": "success" if not desc_stats.get("interrupted") else "interrupted",
                    "artifact_dir": str(artifact_dir),
                    "generated_count": desc_stats["generated_count"],
                    "error_count": desc_stats["error_count"],
                    "remaining_todo": remaining,
                    "message": (
                        f"Generated {desc_stats['generated_count']} descriptions. "
                        f"{remaining} functions still need descriptions."
                        + (" Run again with mode='resume' to continue."
                           if remaining > 0 else "")
                    ),
                }

            else:  # mode == "enhance"
                if not (artifact_dir / "api_docs" / "modules").exists():
                    raise ToolError(
                        "No API docs found. Run with mode='full' first."
                    )

                if progress_cb:
                    progress_cb("Generating module summaries and usage workflows...", 0.0)

                result = enhance_api_docs_step(
                    artifact_dir=artifact_dir,
                    progress_cb=progress_cb,
                )

                return {
                    "status": "success" if not result.get("interrupted") else "interrupted",
                    "artifact_dir": str(artifact_dir),
                    **result,
                    "message": (
                        f"Enhanced {result['generated_count']} modules with summaries and workflows."
                    ),
                }

        except ToolError:
            raise
        except Exception as exc:
            logger.exception("API docs generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # prepare_guidance
    # -------------------------------------------------------------------------

    async def _handle_prepare_guidance(
        self,
        design_doc: str,
    ) -> dict[str, Any]:
        """Run the internal GuidanceAgent to produce a code generation guidance file."""
        self._require_active()

        llm = create_llm_backend()
        if not llm.available:
            raise ToolError(
                "LLM not configured. Set one of: LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY to use prepare_guidance."
            )

        from terrain.domains.upper.guidance.agent import GuidanceAgent
        from terrain.domains.upper.guidance.toolset import MCPToolSet

        tool_set = MCPToolSet(
            semantic_service=self._semantic_service,
            cypher_gen=self._cypher_gen,
            ingestor_factory=self._temporary_ingestor,
            artifact_dir=self._active_artifact_dir,
        )
        agent = GuidanceAgent(toolset=tool_set, llm=llm)

        try:
            guidance = await agent.run(design_doc)
        except Exception as exc:
            logger.exception("Guidance generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

        return {"guidance": guidance}

    # -------------------------------------------------------------------------
    # find_callers — find all functions that call a specific function
    # -------------------------------------------------------------------------

    async def _handle_find_callers(self, function_name: str) -> dict[str, Any]:
        """Find all functions that call the given function via the CALLS graph."""
        self._require_active()

        # Query both Function and Method nodes as callers/callees
        cypher = """
            MATCH (caller)-[:CALLS]->(callee)
            WHERE callee.qualified_name = $name
               OR callee.name = $name
            RETURN DISTINCT
                   caller.qualified_name AS caller_qn,
                   caller.name           AS caller_name,
                   caller.path           AS caller_path,
                   caller.start_line     AS caller_start,
                   caller.end_line       AS caller_end,
                   callee.qualified_name AS callee_qn
        """

        with self._temporary_ingestor() as ingestor:
            rows = ingestor.query(cypher, {"name": function_name})

        if not rows:
            return {
                "function": function_name,
                "caller_count": 0,
                "callers": [],
                "message": f"No callers found for '{function_name}'.",
            }

        callers = []
        for r in rows:
            callers.append({
                "qualified_name": r.get("caller_qn", ""),
                "name": r.get("caller_name", ""),
                "path": r.get("caller_path", ""),
                "start_line": r.get("caller_start"),
                "end_line": r.get("caller_end"),
            })

        # Deduplicate (same caller may appear via qualified_name + name match)
        seen = set()
        unique: list[dict] = []
        for c in callers:
            key = c["qualified_name"]
            if key and key not in seen:
                seen.add(key)
                unique.append(c)

        # Use the matched callee qualified_name for clarity
        matched_qn = rows[0].get("callee_qn", function_name)

        return {
            "function": matched_qn,
            "caller_count": len(unique),
            "callers": unique,
        }

    # -------------------------------------------------------------------------
    # Variable-usage helper — resolve a C/C++ variable symbol and list every
    # read/write usage. Invoked from _handle_find_symbol_in_docs when the caller
    # opts into AST-level mode by supplying `mode` or `qualified_scope`.
    # -------------------------------------------------------------------------

    async def _find_symbol_variable_usage(
        self,
        symbol: str,
        mode: str = "all",
        qualified_scope: str | None = None,
    ) -> dict[str, Any]:
        """Find read/write usages of a C/C++ variable symbol via AST scan.

        mode=``read`` / ``write`` / ``all`` select which categories to scan.
        ``qualified_scope`` restricts results to a single function or module qn
        (or a dot-segment suffix thereof); unknown scopes yield
        ``error="scope not found: <scope>"``.
        """
        self._require_active()

        if mode not in ("read", "write", "all"):
            raise ToolError(
                f"mode must be one of 'read', 'write', 'all' (got {mode!r})"
            )

        assert self._active_repo_path is not None
        repo_path = self._active_repo_path

        is_qualified = "." in symbol
        simple_name = symbol.rsplit(".", 1)[-1] if is_qualified else symbol

        parsers = _symbol_usage_get_parsers()
        source_files = _symbol_usage_list_source_files(repo_path)

        # ---------- Phase 1: resolve symbol → qualified declaration ----------
        all_decls: list[dict[str, Any]] = []
        for abs_path, lang_key in source_files:
            parser = parsers.get(lang_key)
            if parser is None:
                continue
            try:
                source_bytes = abs_path.read_bytes()
            except OSError:
                continue
            tree = parser.parse(source_bytes)
            module_qn = _symbol_usage_module_qn(repo_path, abs_path)
            rel = str(abs_path.relative_to(repo_path))
            all_decls.extend(
                _symbol_usage_collect_declarations(
                    tree.root_node, simple_name, module_qn, rel
                )
            )

        if not all_decls:
            return {
                "success": False,
                "error": "symbol not found",
                "symbol": symbol,
            }

        # Partition by kind — we only accept variable kinds (global, static_local).
        variable_decls = [d for d in all_decls if d["kind"] in ("global", "static_local")]
        non_variable_decls = [d for d in all_decls if d["kind"] not in ("global", "static_local")]

        if not variable_decls and non_variable_decls:
            bad_kind = non_variable_decls[0]["kind"]
            return {
                "success": False,
                "error": f"symbol is not a variable (kind={bad_kind})",
                "symbol": symbol,
            }

        # If the user passed a qualified_name, keep only exact matches.
        if is_qualified:
            exact = [d for d in variable_decls if d["qualified_name"] == symbol]
            if not exact:
                return {
                    "success": False,
                    "error": "symbol not found",
                    "symbol": symbol,
                }
            variable_decls = exact

        # De-duplicate (same variable can be declared in both a .h and .c).
        seen_qn: dict[str, dict[str, Any]] = {}
        for d in variable_decls:
            seen_qn.setdefault(d["qualified_name"], d)
        uniq = list(seen_qn.values())

        if len(uniq) > 1:
            return {
                "success": False,
                "error": "ambiguous",
                "symbol": symbol,
                "candidates": sorted(d["qualified_name"] for d in uniq),
            }

        matched = uniq[0]

        # ---------- Phase 2: read + write scan (single traversal per file) ----------
        want_reads = mode in ("read", "all")
        want_writes = mode in ("write", "all")

        reads: list[dict[str, Any]] = []
        writes: list[dict[str, Any]] = []
        all_scopes: set[str] = set()

        for abs_path, lang_key in source_files:
            parser = parsers.get(lang_key)
            if parser is None:
                continue
            try:
                source_bytes = abs_path.read_bytes()
            except OSError:
                continue
            tree = parser.parse(source_bytes)
            module_qn = _symbol_usage_module_qn(repo_path, abs_path)
            rel = str(abs_path.relative_to(repo_path))
            source_lines = source_bytes.decode("utf-8", errors="replace").splitlines()

            all_scopes |= _symbol_usage_collect_scopes(tree.root_node, module_qn)

            if want_reads:
                reads.extend(
                    _symbol_usage_collect_reads(
                        tree.root_node,
                        simple_name,
                        source_lines,
                        module_qn,
                        rel,
                    )
                )
            if want_writes:
                writes.extend(
                    _symbol_usage_collect_writes(
                        tree.root_node,
                        simple_name,
                        source_lines,
                        module_qn,
                        rel,
                    )
                )

        # Static-local symbols: restrict to their owning function.
        if matched["kind"] == "static_local":
            owner = matched["qualified_name"].rsplit(".", 1)[0]
            reads = [u for u in reads if u["enclosing_function"] == owner]
            writes = [u for u in writes if u["enclosing_function"] == owner]

        # qualified_scope: resolve to one or more valid qns, then filter usages.
        if qualified_scope is not None:
            if qualified_scope in all_scopes:
                matching_qns = {qualified_scope}
            else:
                suffix_marker = "." + qualified_scope
                matching_qns = {s for s in all_scopes if s.endswith(suffix_marker)}
            if not matching_qns:
                return {
                    "success": False,
                    "error": f"scope not found: {qualified_scope}",
                    "symbol": symbol,
                }

            def _in_scope(u: dict[str, Any]) -> bool:
                enc = u["enclosing_function"]
                return any(
                    enc == q or enc.startswith(q + ".") for q in matching_qns
                )

            reads = [u for u in reads if _in_scope(u)]
            writes = [u for u in writes if _in_scope(u)]

        if mode == "read":
            usages = reads
        elif mode == "write":
            usages = writes
        else:
            usages = reads + writes

        def _loc_key(u: dict[str, Any]) -> tuple[str, int]:
            loc = u["location"]
            if ":" in loc:
                p, l = loc.rsplit(":", 1)
                try:
                    return (p, int(l))
                except ValueError:
                    return (p, 0)
            return (loc, 0)

        usages.sort(key=_loc_key)

        return {
            "success": True,
            "symbol": symbol,
            "matched": {
                "qualified_name": matched["qualified_name"],
                "kind": matched["kind"],
                "path": matched["path"],
                "line": matched["line"],
            },
            "usages": usages,
        }

    # -------------------------------------------------------------------------
    # find_symbol_in_docs — search pre-built API docs for global variable refs
    # -------------------------------------------------------------------------

    async def _handle_find_symbol_in_docs(
        self,
        symbol: str,
        max_results: int = 30,
        mode: str | None = None,
        qualified_scope: str | None = None,
    ) -> dict[str, Any]:
        """Find all functions that reference *symbol* in their '全局变量引用' section.

        At index time, ``_render_func_detail`` writes a ``## 全局变量引用`` section
        listing UPPER_CASE identifiers and ``global`` declarations found in each
        function's source code.  This handler first checks for a pre-built
        ``symbol_index.json`` (O(1) lookup) and falls back to a full scan of
        ``*.md`` files when the index is absent (older repos).

        When the caller supplies ``mode`` or ``qualified_scope`` the handler
        switches to AST-level variable usage mode, resolving the symbol against
        the source tree and returning every read/write site for C/C++ globals
        and function-scope statics (previously the ``find_symbol_usage`` tool).
        """
        if mode is not None or qualified_scope is not None:
            return await self._find_symbol_variable_usage(
                symbol,
                mode=mode if mode is not None else "all",
                qualified_scope=qualified_scope,
            )

        self._require_active()

        api_docs_dir = self._active_artifact_dir / "api_docs" / "funcs"
        if not api_docs_dir.exists():
            raise ToolError(
                "API docs not found. Re-run `terrain index <path>` to rebuild them "
                "with global variable extraction enabled."
            )

        import json
        import re

        max_results = max(1, min(max_results, 200))

        index_path = self._active_artifact_dir / "api_docs" / "symbol_index.json"

        if index_path.exists():
            # ---- Fast path: O(1) lookup via pre-built index ----
            try:
                index_data = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Index is corrupt or unreadable — fall through to slow scan
                index_data = None

        if index_path.exists() and index_data is not None:
            meta = index_data.get("_meta", {})
            matching_qns: list[str] = index_data.get(symbol, [])

            results: list[dict[str, Any]] = []
            for qn in matching_qns[:max_results]:
                from terrain.domains.upper.apidoc.api_doc_generator import (
                    _sanitise_filename,
                )
                doc_path = api_docs_dir / f"{_sanitise_filename(qn)}.md"
                func_name = ""
                location = ""
                module_qn = ""
                global_vars_section = ""
                try:
                    content = doc_path.read_text(encoding="utf-8", errors="replace")
                    title_m = re.search(r"^# (.+)$", content, re.MULTILINE)
                    if title_m:
                        func_name = title_m.group(1).strip()
                    loc_m = re.search(r"^- 位置: (.+)$", content, re.MULTILINE)
                    if loc_m:
                        location = loc_m.group(1).strip()
                    mod_m = re.search(r"^- 模块: ([^\n—]+)", content, re.MULTILINE)
                    if mod_m:
                        module_qn = mod_m.group(1).strip()
                    gv_m = re.search(
                        r"^## 全局变量引用\n(.*?)(?=^## |\Z)",
                        content,
                        re.MULTILINE | re.DOTALL,
                    )
                    if gv_m:
                        global_vars_section = gv_m.group(1).strip()
                except OSError:
                    pass
                results.append({
                    "qualified_name": qn,
                    "name": func_name,
                    "module": module_qn,
                    "location": location,
                    "global_vars": global_vars_section,
                })

            if not results:
                return {
                    "symbol": symbol,
                    "match_count": 0,
                    "results": [],
                    "message": (
                        f"No functions found referencing '{symbol}'. "
                        "If the repository was indexed before this feature was added, "
                        "re-run `terrain index <path>` to rebuild the API docs."
                    ),
                }

            response: dict[str, Any] = {
                "symbol": symbol,
                "match_count": len(results),
                "results": results,
            }
            if meta.get("funcs_without_globals", 0) > 0:
                response["warning"] = (
                    f"{meta['funcs_without_globals']} function(s) lack a "
                    "'全局变量引用' section (indexed before this feature was added). "
                    "Re-run `terrain index <path>` for complete coverage."
                )
            return response

        # ---- Slow path: full scan (index absent — older repos) ----
        sym_escaped = re.escape(symbol)
        # Matches a list item exactly like "- `SYMBOL`" (with optional trailing whitespace)
        line_pattern = re.compile(r"^- `" + sym_escaped + r"`\s*$", re.MULTILINE)

        results = []

        for doc_path in sorted(api_docs_dir.glob("*.md")):
            if len(results) >= max_results:
                break
            try:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if not line_pattern.search(content):
                continue

            # Parse key fields from the doc header
            func_name = ""
            location = ""
            module_qn = ""
            global_vars_section = ""

            # Title line: "# FunctionName"
            title_m = re.search(r"^# (.+)$", content, re.MULTILINE)
            if title_m:
                func_name = title_m.group(1).strip()

            # Location: "- 位置: path:start-end"
            loc_m = re.search(r"^- 位置: (.+)$", content, re.MULTILINE)
            if loc_m:
                location = loc_m.group(1).strip()

            # Module: "- 模块: module.qn"
            mod_m = re.search(r"^- 模块: ([^\n—]+)", content, re.MULTILINE)
            if mod_m:
                module_qn = mod_m.group(1).strip()

            # Extract the full 全局变量引用 section for context
            gv_m = re.search(
                r"^## 全局变量引用\n(.*?)(?=^## |\Z)",
                content,
                re.MULTILINE | re.DOTALL,
            )
            if gv_m:
                global_vars_section = gv_m.group(1).strip()

            # Derive qualified name from the filename (reverse of _sanitise_filename)
            # The filename IS the sanitised qualified name.
            qualified_name = doc_path.stem

            results.append({
                "qualified_name": qualified_name,
                "name": func_name,
                "module": module_qn,
                "location": location,
                "global_vars": global_vars_section,
            })

        if not results:
            return {
                "symbol": symbol,
                "match_count": 0,
                "results": [],
                "message": (
                    f"No functions found referencing '{symbol}'. "
                    "If the repository was indexed before this feature was added, "
                    "re-run `terrain index <path>` to rebuild the API docs."
                ),
            }

        return {
            "symbol": symbol,
            "match_count": len(results),
            "results": results,
            "hint": (
                "symbol_index.json not found; re-run `terrain index <path>` "
                "for faster lookups."
            ),
        }

    # -------------------------------------------------------------------------
    # get_config — show current server configuration
    # -------------------------------------------------------------------------

    async def _handle_get_config(self) -> dict[str, Any]:
        """Return current MCP server configuration for debugging and verification."""
        import os as _os

        def _mask(val: str | None) -> str:
            """Mask API key for security: show first 4 and last 4 chars."""
            if not val:
                return "(not set)"
            if len(val) < 10:
                return "****"
            return val[:4] + "****" + val[-4:]

        # --- LLM configuration ---
        llm = create_llm_backend()
        llm_config: dict[str, Any] = {
            "available": llm.available,
            "model": llm.model,
            "base_url": llm.base_url,
            "api_key": _mask(llm.api_key),
        }

        # Detect which provider env var was used
        from terrain.domains.upper.rag.llm_backend import _PROVIDER_ENVS
        detected_provider = None
        for key_env, *_ in _PROVIDER_ENVS:
            if _os.environ.get(key_env):
                detected_provider = key_env
                break
        llm_config["detected_via"] = detected_provider or "(none)"

        # --- Embedding configuration ---
        embedding_config: dict[str, Any] = {}
        try:
            from terrain.domains.core.embedding.qwen3_embedder import create_embedder
            embedder = create_embedder()
            embedder_type = type(embedder).__name__

            embedding_config["provider"] = embedder_type
            if hasattr(embedder, "model"):
                embedding_config["model"] = embedder.model
            if hasattr(embedder, "base_url"):
                embedding_config["base_url"] = embedder.base_url
            if hasattr(embedder, "api_key"):
                embedding_config["api_key"] = _mask(embedder.api_key)
            embedding_config["dimension"] = embedder.get_embedding_dimension()
            embedding_config["available"] = True
        except Exception as exc:
            embedding_config["available"] = False
            embedding_config["error"] = str(exc)

        # Detect embedding provider source
        embed_provider = _os.environ.get("EMBEDDING_PROVIDER", "")
        if not embed_provider:
            if _os.environ.get("DASHSCOPE_API_KEY"):
                embed_provider = "qwen3 (auto-detected via DASHSCOPE_API_KEY)"
            elif _os.environ.get("EMBEDDING_API_KEY") or _os.environ.get("OPENAI_API_KEY"):
                embed_provider = "openai (auto-detected)"
            else:
                embed_provider = "(none)"
        embedding_config["detected_via"] = embed_provider

        # --- Workspace ---
        workspace_config: dict[str, Any] = {
            "path": str(self._workspace),
            "active_repo": str(self._active_repo_path) if self._active_repo_path else None,
            "active_artifact_dir": str(self._active_artifact_dir) if self._active_artifact_dir else None,
        }

        # --- Service status ---
        # Check if graph database exists (without opening persistent connection)
        has_graph = (
            self._active_artifact_dir is not None
            and (self._active_artifact_dir / "graph.db").exists()
        )
        services: dict[str, bool] = {
            "graph_database": has_graph,
            "cypher_query": self._cypher_gen is not None and has_graph,
            "semantic_search": self._semantic_service is not None,
            "file_editor": self._file_editor is not None,
        }

        # --- Environment variable overview ---
        env_keys = [
            "TERRAIN_WORKSPACE",
            "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
            "MOONSHOT_API_KEY", "MOONSHOT_MODEL",
            "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
            "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
            "EMBEDDING_PROVIDER",
        ]
        env_status: dict[str, str] = {}
        for key in env_keys:
            val = _os.environ.get(key)
            if val is None:
                env_status[key] = "(not set)"
            elif "KEY" in key:
                env_status[key] = _mask(val)
            else:
                env_status[key] = val

        return {
            "llm": llm_config,
            "embedding": embedding_config,
            "workspace": workspace_config,
            "services": services,
            "environment_variables": env_status,
        }

    # -------------------------------------------------------------------------
    # reload_config — hot-reload .env and rebuild services
    # -------------------------------------------------------------------------

    async def _handle_reload_config(self) -> dict[str, Any]:
        """Hot-reload configuration and rebuild LLM/embedding services."""
        from terrain.foundation.utils.settings import reload_env

        # 1. Reload environment variables from .env / settings.json
        changes = reload_env(workspace=self._workspace)
        updated = changes.get("updated", [])
        removed = changes.get("removed", [])

        # 2. Rebuild LLM and embedding services with new config
        services_before: dict[str, bool] = {
            "llm": self._cypher_gen is not None,
            "semantic_search": self._semantic_service is not None,
        }

        if self._active_artifact_dir and self._active_artifact_dir.exists():
            try:
                self._load_services(self._active_artifact_dir)
            except Exception as exc:
                return {
                    "status": "partial",
                    "env_changes": {"updated": updated, "removed": removed},
                    "error": f"Environment reloaded but service rebuild failed: {exc}",
                }

        services_after: dict[str, bool] = {
            "llm": self._cypher_gen is not None,
            "semantic_search": self._semantic_service is not None,
        }

        # 3. Build result summary
        service_changes: list[str] = []
        for svc, was_on in services_before.items():
            is_on = services_after[svc]
            if not was_on and is_on:
                service_changes.append(f"{svc}: ✗ → ✓")
            elif was_on and not is_on:
                service_changes.append(f"{svc}: ✓ → ✗")

        return {
            "status": "ok",
            "env_changes": {
                "updated": updated,
                "removed": removed,
            },
            "service_changes": service_changes if service_changes else ["no changes"],
            "services": services_after,
            "hint": (
                "Configuration reloaded. "
                + (f"{len(updated)} key(s) updated. " if updated else "No env changes. ")
                + ("Services rebuilt successfully." if self._active_artifact_dir else "No active repo — services not rebuilt.")
            ),
        }

    # -------------------------------------------------------------------------
    # trace_call_chain — upward call chain tracing
    # -------------------------------------------------------------------------

    async def _handle_trace_call_chain(
        self,
        target_function: str,
        max_depth: int = 10,
        save_wiki: bool = True,
        paths_per_entry_point: int = 20,
    ) -> dict[str, Any]:
        """Trace the upward call chain of a target function."""
        from terrain.domains.core.search.graph_query import GraphQueryService
        from terrain.domains.upper.calltrace.tracer import trace_call_chain
        from terrain.domains.upper.calltrace.formatter import format_trace_result

        self._require_active()

        with self._temporary_ingestor() as ingestor:
            query_service = GraphQueryService(ingestor, backend="kuzu")
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: trace_call_chain(
                    query_service=query_service,
                    target_function=target_function,
                    max_depth=max_depth,
                    paths_per_entry_point=paths_per_entry_point,
                ),
            )

        wiki_pages: list[str] = []
        wiki_contents: list[str] = []
        if save_wiki and self._active_artifact_dir is not None:
            from terrain.domains.upper.calltrace.wiki_writer import write_wiki_pages

            repo_name = self._active_repo_path.name if self._active_repo_path else "unknown"
            repo_root = self._active_repo_path or Path(".")
            written = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: write_wiki_pages(
                    result=result,
                    artifact_dir=self._active_artifact_dir,
                    repo_root=repo_root,
                    repo_name=repo_name,
                ),
            )
            wiki_pages = [str(p) for p in written]
            # Read back wiki content so the agent sees <!-- FILL --> placeholders
            # directly in the response — this is the key to ensuring the agent
            # continues to fill them in rather than stopping here.
            for wp in written:
                try:
                    wiki_contents.append(wp.read_text(encoding="utf-8"))
                except Exception:
                    wiki_contents.append("")

        output: dict[str, Any] = {
            "status": "pending_fill" if wiki_pages else "complete",
            "query": result.query_name,
            "matches": len(result.results),
            "results": [],
        }

        for sr in result.results:
            entry: dict[str, Any] = {
                "target": sr.target.qualified_name,
                "direct_callers": len(sr.direct_callers),
                "entry_points": len(sr.entry_points),
                "total_paths": len(sr.paths),
                "max_depth_reached": sr.max_depth_reached,
                "truncated": sr.truncated,
                "tree": format_trace_result(result) if len(result.results) == 1 else "",
            }
            output["results"].append(entry)

        if wiki_pages:
            for i, wp in enumerate(wiki_pages):
                if i < len(output["results"]):
                    output["results"][i]["wiki_page"] = wp
                    if i < len(wiki_contents):
                        output["results"][i]["wiki_content"] = wiki_contents[i]

            # Count unfilled placeholders so the agent knows exactly how many
            total_fills = sum(c.count("<!-- FILL") for c in wiki_contents)

            output["action_required"] = {
                "what": (
                    f"The wiki worksheets contain {total_fills} unfilled "
                    "<!-- FILL --> placeholders that you MUST complete NOW."
                ),
                "how": [
                    "Look at the 'wiki_content' field above — every <!-- FILL --> "
                    "or <!-- FILL: ... --> marker is a field you need to populate.",
                    "Use get_code_snippet or get_api_doc to read the source code "
                    "of each function listed in the call chain paths.",
                    "Replace each <!-- FILL --> with your analysis based on the "
                    "source code (trigger scenarios, conditions, call frequency, "
                    "key parameters, log output, path summaries, exception branches).",
                    "Write the completed markdown back to the wiki files "
                    "listed in 'wiki_page' fields.",
                    "Summarize your findings to the user in the chat.",
                ],
                "wiki_files": wiki_pages,
                "unfilled_count": total_fills,
            }
            output["user_hint"] = (
                f"已生成 {len(wiki_pages)} 个调用链分析工作表，"
                f"包含 {total_fills} 个待填充字段。"
                "正在自动分析源码并补全..."
            )

        return output

    # -------------------------------------------------------------------------
    # get_merge_diff — functions changed between two merge commits
    # -------------------------------------------------------------------------

    async def _handle_get_merge_diff(
        self,
        from_merge: str | None = None,
        to_merge: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Return functions that changed between two merge commits."""
        self._require_active()

        repo_path, artifact_dir = self._active_repo_path, self._active_artifact_dir

        detector = _GCD()

        # Auto-discover merge commits when not supplied
        if from_merge is None or to_merge is None:
            merges = await asyncio.to_thread(
                detector.get_merge_commits, repo_path, 2, branch
            )
            if len(merges) < 2:
                raise ToolError(
                    "Less than 2 merge commits found in history. "
                    "Supply explicit from_merge and to_merge SHAs, or make sure "
                    "the repository has at least two merge commits."
                )
            to_merge = to_merge or merges[0]
            from_merge = from_merge or merges[1]

        # Compute changed files
        changed_files = await asyncio.to_thread(
            detector.get_changed_files_between, repo_path, from_merge, to_merge
        )
        if changed_files is None:
            raise ToolError(
                f"One or both commits not in git history: '{from_merge[:12]}' / '{to_merge[:12]}'. "
                "Make sure the SHAs exist in this repository."
            )

        # Convert absolute paths → relative paths for kuzu query
        rel_paths: list[str] = []
        for f in changed_files:
            try:
                rel_paths.append(str(f.relative_to(repo_path)))
            except ValueError:
                rel_paths.append(str(f))

        _SYMBOL_LABELS = [
            "Function", "Method", "Class", "Interface", "Enum", "Type", "Union"
        ]

        functions: list[dict[str, Any]] = []
        if rel_paths:
            with self._temporary_ingestor() as ingestor:
                for label in _SYMBOL_LABELS:
                    cypher = (
                        f"MATCH (f:{label}) WHERE f.path IN $paths "
                        "RETURN f.qualified_name AS qn, f.name AS fname, "
                        "f.path AS fpath, f.start_line AS start"
                    )
                    rows = ingestor.query(cypher, {"paths": rel_paths})
                    for row in rows:
                        functions.append({
                            "qualified_name": row.get("qn", ""),
                            "name": row.get("fname", ""),
                            "file_path": row.get("fpath", ""),
                            "start_line": row.get("start"),
                            "node_type": label,
                        })

        return {
            "from_merge": from_merge,
            "to_merge": to_merge,
            "changed_files": len(rel_paths),
            "functions": functions,
        }

    # -------------------------------------------------------------------------
    # extract_predicates — predicate skeleton for a C function (slice 1/3)
    # -------------------------------------------------------------------------

    async def _handle_extract_predicates(
        self,
        qualified_name: str,
    ) -> dict[str, Any]:
        """Extract predicates (if / while / for / switch_case / ternary) from a
        C function's AST subtree.

        Slice 1 MVP: only the skeleton — kind / location / expression /
        nesting_path. ``symbols_referenced``, ``guarded_block``,
        ``contains_assignments``, ``contains_calls``, ``has_early_return`` are
        reserved for slice 2/3.
        """
        self._require_active()
        assert self._active_repo_path is not None
        repo_path = self._active_repo_path

        bundle = _extract_predicates_bundle()
        parser = bundle["parser"]
        predicate_query = bundle["predicate_query"]
        function_query = bundle["function_query"]
        call_query = bundle.get("call_query")
        if parser is None or predicate_query is None or function_query is None:
            raise ToolError(
                "C parser or predicate query unavailable. "
                "Ensure tree-sitter-c is installed."
            )

        simple_name = qualified_name.rsplit(".", 1)[-1]

        from tree_sitter import QueryCursor
        from terrain.foundation.parsers.predicate_processor import extract_predicates

        matches: list[tuple[Any, Path]] = []  # (function_node, abs_path)
        for abs_path in _extract_predicates_list_c_files(repo_path):
            try:
                source_bytes = abs_path.read_bytes()
            except OSError:
                continue
            tree = parser.parse(source_bytes)
            cursor = QueryCursor(function_query)
            captures = cursor.captures(tree.root_node)
            for func_node in captures.get("function", []):
                name = _extract_predicates_function_name(func_node)
                if name == simple_name:
                    matches.append((func_node, abs_path))

        if not matches:
            return {
                "success": False,
                "error": "function not found",
                "function": qualified_name,
            }

        if len(matches) > 1:
            candidates = []
            for _node, abs_path in matches:
                try:
                    rel = str(abs_path.relative_to(repo_path))
                except ValueError:
                    rel = str(abs_path)
                candidates.append(f"{rel}:{_node.start_point[0] + 1}")
            return {
                "success": False,
                "error": "ambiguous",
                "function": qualified_name,
                "candidates": sorted(candidates),
            }

        func_node, abs_path = matches[0]
        try:
            rel_file_path = str(abs_path.relative_to(repo_path))
        except ValueError:
            rel_file_path = str(abs_path)

        predicates = extract_predicates(
            func_node, predicate_query, rel_file_path, call_query=call_query
        )

        return {
            "success": True,
            "function": qualified_name,
            "predicates": predicates,
        }
