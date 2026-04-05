"""Generate per-module and per-function API documentation from the knowledge graph.

Output layout (all under ``{artifact_dir}/api_docs/``):

    index.md              — L1 index: one row per module with summary counts
    modules/
        {module_qn}.md    — L2 index: all interfaces in one module
    funcs/
        {func_qn}.md      — L3 detail: signature, docstring, call graph
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "_unpack_row",
    "_build_call_graph",
    "_read_source_snippet",
    "_build_call_tree",
    "_extract_usage_snippet",
    "_infer_ownership",
    "_sanitise_filename",
    "_render_func_detail",
    "_render_module_page",
    "_render_index",
    "generate_api_docs",
]


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _unpack_row(row: dict[str, Any]) -> list[Any]:
    """Normalise a Kùzu result row to a flat list.

    Handles two formats:
    - Legacy: {"result": [v1, v2, ...]}
    - Named-column: {"col1": v1, "col2": v2, ...}  (from KuzuIngestor.query())
    """
    if "result" in row:
        raw = row["result"]
        return list(raw) if isinstance(raw, (list, tuple)) else [raw]
    return list(row.values())


def _build_call_graph(
    call_rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Build bidirectional call-graph mappings.

    Returns:
        (callers_of, callees_of) where each maps
        qualified_name → list of {qn, path, start_line}.
    """
    callers_of: dict[str, list[dict]] = defaultdict(list)
    callees_of: dict[str, list[dict]] = defaultdict(list)
    seen_edges: set[tuple[str, str]] = set()

    for row in call_rows:
        r = _unpack_row(row)
        if len(r) < 2:
            continue
        caller_qn, callee_qn = r[0], r[1]
        edge_key = (caller_qn, callee_qn)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        callee_path = r[2] if len(r) > 2 else None
        callee_start = r[3] if len(r) > 3 else None

        # Caller location (returned by extended _CALLS_QUERY)
        caller_path = r[4] if len(r) > 4 else None
        caller_start = r[5] if len(r) > 5 else None
        caller_end = r[6] if len(r) > 6 else None

        callees_of[caller_qn].append({
            "qn": callee_qn,
            "path": callee_path,
            "start_line": callee_start,
        })
        callers_of[callee_qn].append({
            "qn": caller_qn,
            "path": caller_path,
            "start_line": caller_start,
            "end_line": caller_end,
        })

    return callers_of, callees_of


# ---------------------------------------------------------------------------
# Source code & call tree helpers
# ---------------------------------------------------------------------------

def _read_source_snippet(
    path: str | None,
    start_line: int | None,
    end_line: int | None,
    repo_path: Path | None = None,
) -> str | None:
    """Read function source code from the file system.

    Returns the source code string or None if file cannot be read.
    """
    if not path or not start_line or not end_line:
        return None

    # Try absolute path first, then relative to repo_path
    file_path = Path(path)
    if not file_path.is_absolute() and repo_path:
        file_path = repo_path / path

    if not file_path.exists():
        return None

    try:
        from ..utils.encoding import read_source_lines
        lines = read_source_lines(file_path)
        # start_line and end_line are 1-based
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        snippet = "\n".join(lines[start:end])
        # Truncate very long functions
        if len(snippet) > 3000:
            snippet = snippet[:3000] + "\n    /* ... truncated ... */"
        return snippet
    except (OSError, UnicodeDecodeError):
        return None


def _build_call_tree(
    qn: str,
    callees_of: dict[str, list[dict]],
    func_lookup: dict[str, dict],
    depth: int = 2,
    _visited: set | None = None,
) -> list[str]:
    """Build ASCII call tree lines for a function, up to `depth` levels.

    Returns list of strings like:
        ["├── func_b          [static]", "│   └── func_c", "└── func_d"]
    """
    if _visited is None:
        _visited = set()

    _visited.add(qn)
    callees = callees_of.get(qn, [])
    lines: list[str] = []

    for i, callee in enumerate(callees):
        callee_qn = callee["qn"]
        callee_func = func_lookup.get(callee_qn, {})
        name = callee_func.get("name", callee_qn.rsplit(".", 1)[-1])
        vis = callee_func.get("visibility", "")
        vis_tag = f"  [{vis}]" if vis and vis != "public" else ""

        is_last = (i == len(callees) - 1)
        prefix = "└── " if is_last else "├── "
        lines.append(f"{prefix}{name}{vis_tag}")

        # Recurse if not visited and within depth
        if depth > 1 and callee_qn not in _visited:
            sub_lines = _build_call_tree(
                callee_qn, callees_of, func_lookup, depth - 1, _visited
            )
            child_prefix = "    " if is_last else "│   "
            for sub_line in sub_lines:
                lines.append(f"{child_prefix}{sub_line}")

    return lines


def _extract_usage_snippet(
    func_name: str,
    caller: dict[str, Any],
    repo_path: Path | None = None,
    context_lines: int = 3,
    max_snippet_chars: int = 600,
) -> str | None:
    """Extract a code snippet from a caller function showing how it calls *func_name*.

    Reads the caller's source file, finds lines containing a call to *func_name*,
    and returns surrounding context lines.  Returns None if the call cannot be found.

    Args:
        func_name: Simple name of the called function (e.g., "parse_btype").
        caller: Caller dict with "path", "start_line", "end_line".
        repo_path: Repository root for resolving relative paths.
        context_lines: Number of lines before/after the call to include.
        max_snippet_chars: Maximum characters for the snippet.
    """
    caller_path = caller.get("path")
    caller_start = caller.get("start_line")
    caller_end = caller.get("end_line")
    if not caller_path or not caller_start or not caller_end:
        return None

    file_path = Path(caller_path)
    if not file_path.is_absolute() and repo_path:
        file_path = repo_path / caller_path

    if not file_path.exists():
        return None

    try:
        from ..utils.encoding import read_source_lines
        all_lines = read_source_lines(file_path)
    except (OSError, UnicodeDecodeError):
        return None

    # Restrict search to the caller function's body
    body_start = max(0, caller_start - 1)
    body_end = min(len(all_lines), caller_end)

    # Find lines containing a call to func_name within the caller body
    call_indices: list[int] = []
    for idx in range(body_start, body_end):
        if func_name in all_lines[idx]:
            call_indices.append(idx)

    if not call_indices:
        return None

    # Use the first call site and extract surrounding context
    call_idx = call_indices[0]
    snippet_start = max(body_start, call_idx - context_lines)
    snippet_end = min(body_end, call_idx + context_lines + 1)
    snippet_lines = all_lines[snippet_start:snippet_end]

    snippet = "\n".join(snippet_lines)
    if len(snippet) > max_snippet_chars:
        snippet = snippet[:max_snippet_chars] + "\n    /* ... truncated ... */"

    return snippet


def _infer_ownership(func: dict[str, Any]) -> list[str]:
    """Infer memory ownership hints from function signature using heuristics.

    Returns list of strings describing ownership for each parameter and return value.
    """
    hints: list[str] = []
    name = func.get("name") or ""
    return_type = func.get("return_type") or ""

    # Return type ownership
    if "*" in return_type:
        if any(kw in name.lower() for kw in ("init", "create", "alloc", "new", "open", "dup", "clone")):
            hints.append(f"返回 `{return_type}`: 调用方拥有，需释放")
        elif any(kw in name.lower() for kw in ("get", "find", "lookup", "peek", "current")):
            hints.append(f"返回 `{return_type}`: 借用，不可释放")

    # Free/destroy patterns
    if any(kw in name.lower() for kw in ("free", "destroy", "release", "close", "cleanup", "deinit")):
        hints.append("释放函数：调用后指针失效")

    return hints


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _sanitise_filename(qn: str) -> str:
    """Convert a qualified name to a safe filename (no path separators).

    macOS / Linux limit filenames to 255 bytes.  For long C signatures that
    include the full parameter list we truncate to 180 chars and append an
    8-char hash so the name stays unique.
    """
    import hashlib
    safe = qn.replace("/", "_").replace("\\", "_").replace("\n", " ").replace("\r", "")
    # Encode to bytes to measure the real byte length (UTF-8)
    encoded = safe.encode("utf-8")
    if len(encoded) <= 200:
        return safe
    # Truncate to 180 bytes (safe UTF-8 boundary) + 8-char hex hash
    truncated = encoded[:180].decode("utf-8", errors="ignore").rstrip()
    suffix = hashlib.md5(qn.encode("utf-8")).hexdigest()[:8]
    return f"{truncated}_{suffix}"


def _render_func_detail(
    func: dict[str, Any],
    callers: list[dict],
    callees: list[dict],
    callees_of: dict[str, list[dict]] | None = None,
    func_lookup: dict[str, dict] | None = None,
    module_desc: str = "",
    repo_path: Path | None = None,
) -> str:
    """Render L3 detail page for a single function (embedding-optimized)."""
    if callees_of is None:
        callees_of = {}
    if func_lookup is None:
        func_lookup = {}

    lines: list[str] = []
    qn = func["qn"]
    name = func.get("name") or qn.rsplit(".", 1)[-1]
    module_qn = func.get("module_qn", "")
    kind = func.get("kind") or ""

    # Title
    lines.append(f"# {name}")
    lines.append("")

    # Semantic description line — the most important line for embedding retrieval
    # Supports bilingual descriptions (Chinese/English) for better cross-language search
    doc = (func.get("docstring") or "").strip()

    # Parse bilingual description format
    import re
    chinese_desc = ""
    english_desc = ""

    if doc:
        # Look for explicit bilingual markers
        chinese_match = re.search(r'(?:中文[:：]\s*)([^\n]+)', doc)
        english_match = re.search(r'(?:English[:：]\s*)([^\n]+)', doc)

        if chinese_match:
            chinese_desc = chinese_match.group(1).strip()
        if english_match:
            english_desc = english_match.group(1).strip()

        # If no explicit markers, use the whole doc as-is
        if not chinese_desc and not english_desc:
            first_sentence = doc.split(".")[0].strip() + "." if "." in doc else doc
            lines.append(f"> {first_sentence}")
        else:
            # Display bilingual description
            if chinese_desc:
                lines.append(f"> 中文：{chinese_desc}")
            if english_desc:
                lines.append(f"> English：{english_desc}")
    else:
        lines.append(f"> <!-- TODO: LLM generate description for {name} -->")
    lines.append("")

    # Metadata block
    sig = func.get("signature") or name
    if kind == "macro":
        lines.append("- 类型: 宏定义")
        lines.append(f"- 定义: `{sig}`")
    else:
        lines.append(f"- 签名: `{sig}`")
        if func.get("return_type"):
            lines.append(f"- 返回: `{func['return_type']}`")

    vis = func.get("visibility") or "unknown"
    loc_path = func.get("path") or ""
    start = func.get("start_line") or "?"
    end = func.get("end_line") or "?"

    # Determine if declared in header
    header_note = ""
    if vis == "public" and loc_path:
        header_name = Path(loc_path).stem + ".h"
        header_note = f" | 头文件: {header_name}"

    lines.append(f"- 可见性: {vis}{header_note}")
    lines.append(f"- 位置: {loc_path}:{start}-{end}")

    # Module with inline description for embedding context
    if module_desc:
        lines.append(f"- 模块: {module_qn} — {module_desc}")
    else:
        lines.append(f"- 模块: {module_qn}")
    lines.append("")

    # Full docstring (if longer than the summary line or contains bilingual content)
    # Always show full description if bilingual markers are present
    has_bilingual = "中文：" in doc or "English：" in doc
    if doc and (len(doc) > 80 or has_bilingual):
        lines.append("## 描述")
        lines.append("")
        # If we have bilingual markers, format them nicely
        if has_bilingual:
            # Parse and format bilingual description
            import re

            # Extract Chinese description
            chinese_match = re.search(r'中文[：:]\s*([^\n]+(?:\n(?![中文英文]:).*)*)', doc)
            english_match = re.search(r'English[：:]\s*([^\n]+(?:\n(?![中文英文]:).*)*)', doc)

            if chinese_match:
                chinese_text = chinese_match.group(1).strip()
                lines.append(f"**中文：** {chinese_text}")
                lines.append("")
            if english_match:
                english_text = english_match.group(1).strip()
                lines.append(f"**English:** {english_text}")
                lines.append("")
        else:
            lines.append(doc)
        lines.append("")

    # Call tree (2-level, visual)
    tree_lines = _build_call_tree(qn, callees_of, func_lookup, depth=2)
    if tree_lines:
        lines.append("## 调用树")
        lines.append("")
        lines.append(f"{name}")
        lines.extend(tree_lines)
        lines.append("")

    # Called by
    lines.append(f"## 被调用 ({len(callers)})")
    lines.append("")
    if callers:
        for c in callers:
            caller_func = func_lookup.get(c["qn"], {})
            caller_module = caller_func.get("module_qn", "")
            module_tag = f" ({caller_module})" if caller_module and caller_module != module_qn else ""
            loc = ""
            if c.get("path") and c.get("start_line"):
                loc = f" → {c['path']}:{c['start_line']}"
            lines.append(f"- {c['qn']}{module_tag}{loc}")
    else:
        lines.append("*(无调用者)*")
    lines.append("")

    # Usage examples — extract real call-site snippets from callers
    if callers and repo_path:
        usage_snippets: list[tuple[str, str, str]] = []  # (caller_name, location, snippet)
        max_examples = 3
        for c in callers:
            if len(usage_snippets) >= max_examples:
                break
            caller_name = c["qn"].rsplit(".", 1)[-1] if "." in c["qn"] else c["qn"]
            snippet = _extract_usage_snippet(name, c, repo_path)
            if snippet:
                caller_loc = ""
                if c.get("path"):
                    fname = Path(c["path"]).name
                    caller_loc = f"{fname}:{c.get('start_line', '?')}"
                usage_snippets.append((caller_name, caller_loc, snippet))

        if usage_snippets:
            lines.append(f"## 使用示例 ({len(usage_snippets)})")
            lines.append("")
            ext = Path(loc_path).suffix if loc_path else ""
            snippet_lang = "cpp" if ext in (".cpp", ".cc", ".cxx", ".hpp") else "c"
            for caller_name, caller_loc, snippet in usage_snippets:
                loc_tag = f" ({caller_loc})" if caller_loc else ""
                lines.append(f"### 在 {caller_name} 中的调用{loc_tag}")
                lines.append("")
                lines.append(f"```{snippet_lang}")
                lines.append(snippet)
                lines.append("```")
                lines.append("")

    # Parameters & memory ownership (C/C++ specific)
    params = func.get("parameters")
    ownership_hints = _infer_ownership(func)
    if (params and isinstance(params, list) and any(p for p in params)) or ownership_hints:
        lines.append("## 参数与内存")
        lines.append("")
        if params and isinstance(params, list):
            lines.append("| 参数 | 方向 | 所有权 |")
            lines.append("|------|------|--------|")
            for p in params:
                if not p:
                    continue
                # Heuristic: const pointer = input/borrow, pointer = in-out
                direction = "in"
                ownership = ""
                p_str = str(p)
                if "*" in p_str:
                    if "const" in p_str:
                        direction = "in"
                        ownership = "借用"
                    else:
                        direction = "in/out"
                        ownership = "借用，可修改"
                lines.append(f"| `{p_str}` | {direction} | {ownership} |")
        lines.append("")
        if ownership_hints:
            for hint in ownership_hints:
                lines.append(f"- {hint}")
            lines.append("")

    # Source code
    if kind != "macro":  # Macros already show definition in sig
        source = _read_source_snippet(
            func.get("path"), func.get("start_line"), func.get("end_line"), repo_path
        )
        if source:
            lines.append("## 实现")
            lines.append("")
            # Detect language from file extension
            ext = Path(loc_path).suffix if loc_path else ""
            lang = "cpp" if ext in (".cpp", ".cc", ".cxx", ".hpp") else "c"
            lines.append(f"```{lang}")
            lines.append(source)
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def _render_module_page(
    module_qn: str,
    files: list[str],
    funcs: list[dict[str, Any]],
    types: list[dict[str, Any]],
    callees_of: dict[str, list[dict]] | None = None,
    func_lookup: dict[str, dict] | None = None,
    module_desc: str = "",
    macros: list[dict[str, Any]] | None = None,
) -> str:
    """Render L2 module index page."""
    if callees_of is None:
        callees_of = {}
    if func_lookup is None:
        func_lookup = {}

    lines: list[str] = []
    lines.append(f"# {module_qn}")
    if module_desc:
        lines.append("")
        lines.append(f"> {module_desc}")
    lines.append("")

    # Header/implementation split
    headers = [f for f in files if f.endswith((".h", ".hpp", ".hxx"))]
    sources = [f for f in files if not f.endswith((".h", ".hpp", ".hxx"))]
    if headers:
        lines.append(f"**头文件**: {', '.join(headers)} | **实现**: {', '.join(sources) if sources else '—'}")
    else:
        lines.append(f"**文件**: {', '.join(files)}")
    lines.append("")

    # funcs now only contains real functions (macros are passed separately)
    regular_funcs = funcs
    if macros is None:
        macros = []

    # Call tree for public entry points
    public_funcs = [f for f in regular_funcs if f.get("visibility") == "public"]
    if public_funcs:
        lines.append("## 调用树")
        lines.append("")
        for pf in public_funcs:
            ret = f" → {pf['return_type']}" if pf.get("return_type") else ""
            lines.append(f"{pf['name']}{ret}")
            tree_lines = _build_call_tree(pf["qn"], callees_of, func_lookup, depth=2)
            lines.extend(tree_lines)
            lines.append("")

    # Group functions by visibility
    by_vis: dict[str, list[dict]] = defaultdict(list)
    for f in regular_funcs:
        by_vis[f.get("visibility") or "unknown"].append(f)

    vis_order = ["public", "extern", "static", "unknown"]
    vis_labels = {
        "public": "公开接口",
        "extern": "外部声明",
        "static": "内部函数",
        "unknown": "其他",
    }

    for vis in vis_order:
        group = by_vis.get(vis)
        if not group:
            continue
        lines.append(f"## {vis_labels.get(vis, vis)} ({len(group)})")
        lines.append("")
        lines.append("| 函数 | 签名 | 一句话 |")
        lines.append("|------|------|--------|")
        for f in group:
            safe = _sanitise_filename(f["qn"])
            sig = f.get("signature") or f["name"]
            doc = (f.get("docstring") or "").strip()
            brief = (doc.split(".")[0].strip() + ".") if doc and "." in doc else (doc or "—")
            if len(brief) > 60:
                brief = brief[:57] + "..."
            lines.append(f"| [{f['name']}](../funcs/{safe}.md) | `{sig}` | {brief} |")
        lines.append("")

    # Types: structs, unions, enums with member info
    if types:
        # Group by kind
        structs = [t for t in types if t.get("kind") in ("struct", None, "")]
        unions = [t for t in types if t.get("kind") == "union"]
        enums = [t for t in types if t.get("kind") == "enum"]
        typedefs = [t for t in types if t.get("kind") == "typedef"]

        if structs or unions:
            lines.append(f"## 结构体 ({len(structs) + len(unions)})")
            lines.append("")
            for t in structs + unions:
                kind_label = "union" if t.get("kind") == "union" else "struct"
                lines.append(f"### {t.get('name', '?')} ({kind_label})")
                lines.append("")
                members = t.get("members") or t.get("parameters")
                if members and isinstance(members, list):
                    for m in members:
                        if m:
                            lines.append(f"- `{m}`")
                else:
                    sig = t.get("signature", "")
                    if sig:
                        lines.append(f"```c\n{sig}\n```")
                lines.append("")

        if enums:
            lines.append(f"## 枚举 ({len(enums)})")
            lines.append("")
            for t in enums:
                lines.append(f"### {t.get('name', '?')}")
                lines.append("")
                members = t.get("members") or t.get("parameters")
                if members and isinstance(members, list):
                    lines.append(f"值: `{' | '.join(str(m) for m in members if m)}`")
                else:
                    sig = t.get("signature", "")
                    if sig:
                        lines.append(f"```c\n{sig}\n```")
                lines.append("")

        if typedefs:
            lines.append(f"## 类型别名 ({len(typedefs)})")
            lines.append("")
            lines.append("| 名称 | 定义 |")
            lines.append("|------|------|")
            for t in typedefs:
                lines.append(f"| {t.get('name', '?')} | `{t.get('signature', '')}` |")
            lines.append("")

    # Macros
    if macros:
        lines.append(f"## 宏 ({len(macros)})")
        lines.append("")
        lines.append("| 宏 | 定义 |")
        lines.append("|----|------|")
        for m in macros:
            sig = m.get("signature") or f"#define {m['name']}"
            # Truncate long macro definitions
            if len(sig) > 80:
                sig = sig[:77] + "..."
            lines.append(f"| {m['name']} | `{sig}` |")
        lines.append("")

    return "\n".join(lines)


def _render_index(
    module_summaries: list[dict[str, Any]],
    total_funcs: int,
    total_types: int,
    import_graph: dict[str, list[str]] | None = None,
) -> str:
    """Render L1 global index page."""
    lines: list[str] = []
    lines.append("# API Documentation Index")
    lines.append("")
    lines.append(f"Total: {len(module_summaries)} modules, "
                 f"{total_funcs} functions, {total_types} types")
    lines.append("")

    # Module table with description column
    lines.append("| 模块 | 职责 | 头文件 | 函数 | 类型 | 宏 |")
    lines.append("|------|------|--------|------|------|----|")

    for m in module_summaries:
        safe = _sanitise_filename(m["qn"])
        # Find header files
        headers = [f for f in m["files"] if f.endswith((".h", ".hpp", ".hxx"))]
        header_str = ", ".join(headers) if headers else "—"
        desc = m.get("desc", "—")
        macro_count = m.get("macros", 0)
        func_count = m["public"] + m["static"] + m["extern"]
        type_count = m["types"]
        lines.append(
            f"| [{m['qn']}](modules/{safe}.md) | {desc} "
            f"| {header_str} | {func_count} | {type_count} | {macro_count} |"
        )
    lines.append("")

    # Include dependency tree
    if import_graph:
        lines.append("## #include 依赖")
        lines.append("")
        # Find root modules (not imported by anyone)
        all_imported: set[str] = set()
        for targets in import_graph.values():
            all_imported.update(targets)
        roots = [m for m in import_graph if m not in all_imported]
        if not roots:
            roots = sorted(import_graph.keys())[:5]

        visited: set[str] = set()

        def _render_tree(mod: str, prefix: str = "", is_last: bool = True) -> None:
            if mod in visited:
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{mod} (已展开)")
                return
            visited.add(mod)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{mod}")
            children = import_graph.get(mod, [])
            for j, child in enumerate(children):
                child_is_last = (j == len(children) - 1)
                child_prefix = prefix + ("    " if is_last else "│   ")
                _render_tree(child, child_prefix, child_is_last)

        for i, root in enumerate(sorted(roots)):
            if i > 0:
                lines.append("")
            lines.append(root)
            children = import_graph.get(root, [])
            for j, child in enumerate(children):
                _render_tree(child, "", j == len(children) - 1)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_api_docs(
    func_rows: list[dict[str, Any]],
    type_rows: list[dict[str, Any]],
    call_rows: list[dict[str, Any]],
    output_dir: Path,
    import_rows: list[dict[str, Any]] | None = None,
    repo_path: Path | None = None,
) -> dict[str, Any]:
    """Generate hierarchical API documentation from pre-fetched graph data.

    Args:
        func_rows: Rows from fetch_all_functions_for_docs query.
        type_rows: Rows from fetch_all_types_for_docs query.
        call_rows: Rows from fetch_all_calls query.
        output_dir: Directory to write api_docs/ into.
        import_rows: Rows from fetch_all_imports query (optional).
        repo_path: Root path of the repository for source reading (optional).

    Returns:
        Summary dict with module_count, func_count, type_count.
    """
    api_dir = output_dir / "api_docs"
    modules_dir = api_dir / "modules"
    funcs_dir = api_dir / "funcs"
    modules_dir.mkdir(parents=True, exist_ok=True)
    funcs_dir.mkdir(parents=True, exist_ok=True)

    callers_of, callees_of = _build_call_graph(call_rows)

    # ---- Group functions by module ----
    # module_qn → {files: set, funcs: list, types: list}
    modules: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"files": set(), "funcs": [], "types": [], "macros": []}
    )
    seen_funcs: set[str] = set()

    for row in func_rows:
        r = _unpack_row(row)
        if len(r) < 11:
            continue
        module_qn = r[0] or "unknown"
        module_path = r[1] or ""
        func_qn = r[2] or ""
        if func_qn in seen_funcs:
            continue
        seen_funcs.add(func_qn)
        func: dict[str, Any] = {
            "module_qn": module_qn,
            "qn": func_qn,
            "name": r[3] or "",
            "signature": r[4],
            "return_type": r[5],
            "visibility": r[6],
            "parameters": r[7],
            "docstring": r[8],
            "start_line": r[9],
            "end_line": r[10],
            "path": (r[11] if len(r) > 11 else None) or module_path,
        }
        # Handle kind field (13th field, index 12)
        if len(r) > 12:
            func["kind"] = r[12]
        modules[module_qn]["files"].add(module_path)
        # Macros go to a separate list; only real functions in "funcs"
        if func.get("kind") == "macro":
            modules[module_qn]["macros"].append(func)
        else:
            modules[module_qn]["funcs"].append(func)

    for row in type_rows:
        r = _unpack_row(row)
        if len(r) < 6:
            continue
        # First column may be qualified_name (e.g., "mod.StructName") or module_qn
        first_col = r[0] or "unknown"
        type_name = r[1] or ""
        # Derive module_qn: if first_col ends with ".type_name", strip it
        if type_name and first_col.endswith(f".{type_name}"):
            module_qn = first_col[: -(len(type_name) + 1)]
        else:
            module_qn = first_col
        type_info: dict[str, Any] = {
            "name": type_name,
            "kind": r[2],
            "signature": r[3],
        }
        # Handle both Class rows (7 fields with parameters) and Type rows (6 fields without)
        if len(r) >= 7:
            type_info["members"] = r[4]  # parameters field contains members/enum values
            type_info["start_line"] = r[5]
            type_info["end_line"] = r[6]
        else:
            type_info["start_line"] = r[4]
            type_info["end_line"] = r[5]
        modules[module_qn]["types"].append(type_info)

    # ---- Build func_lookup for call tree and caller enrichment ----
    func_lookup: dict[str, dict] = {}
    for mod_data in modules.values():
        for func in mod_data["funcs"]:
            if func["qn"]:
                func_lookup[func["qn"]] = func

    # ---- Enrich call graph with path info from func_lookup ----
    # CALLS query may return empty path for stub nodes; fill from DEFINES data
    for _qn, caller_list in callers_of.items():
        for c in caller_list:
            if not c.get("path") and c["qn"] in func_lookup:
                f = func_lookup[c["qn"]]
                c["path"] = f.get("path") or ""
                c["start_line"] = c.get("start_line") or f.get("start_line")
                c["end_line"] = c.get("end_line") or f.get("end_line")
    for _qn, callee_list in callees_of.items():
        for c in callee_list:
            if not c.get("path") and c["qn"] in func_lookup:
                f = func_lookup[c["qn"]]
                c["path"] = f.get("path") or ""
                c["start_line"] = c.get("start_line") or f.get("start_line")

    # ---- Build import graph ----
    import_graph: dict[str, list[str]] = defaultdict(list)
    if import_rows:
        for row in import_rows:
            r = _unpack_row(row)
            if len(r) >= 2:
                import_graph[r[0]].append(r[1])

    # ---- Collect all known files per module ----
    # Since .c and .h share module_qn, we need to discover both file paths.
    # The func rows carry module_path (last-written, typically .c).
    # We also query for any .h counterpart by checking the func paths.
    for mod_data in modules.values():
        paths = set()
        for f in mod_data["funcs"] + mod_data["macros"]:
            p = f.get("path") or ""
            if p:
                paths.add(Path(p).name)
        mod_data["files"].update(paths)
        # Remove empty strings
        mod_data["files"].discard("")

    # ---- Generate L3: per-function detail pages (parallel) ----
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import os

    # Collect all func tasks
    _l3_tasks: list[dict[str, Any]] = []
    for mod_data in modules.values():
        for func in mod_data["funcs"]:
            if func["qn"]:
                _l3_tasks.append(func)

    def _write_l3(func: dict[str, Any]) -> None:
        qn = func["qn"]
        content = _render_func_detail(
            func,
            callers=callers_of.get(qn, []),
            callees=callees_of.get(qn, []),
            callees_of=callees_of,
            func_lookup=func_lookup,
            repo_path=repo_path,
        )
        safe = _sanitise_filename(qn)
        (funcs_dir / f"{safe}.md").write_text(content, encoding="utf-8")

    max_workers = min(os.cpu_count() or 4, 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_write_l3, func) for func in _l3_tasks]
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                logger.warning(f"L3 doc generation error: {exc}")

    total_funcs = len(_l3_tasks)

    # ---- Generate L2: per-module pages (parallel) ----
    def _write_l2(module_qn: str) -> dict[str, Any]:
        mod_data = modules[module_qn]
        funcs = mod_data["funcs"]
        types = mod_data["types"]
        macros = mod_data["macros"]
        files = sorted(mod_data["files"])

        content = _render_module_page(
            module_qn, files, funcs, types,
            callees_of=callees_of,
            func_lookup=func_lookup,
            macros=macros,
        )
        safe = _sanitise_filename(module_qn)
        (modules_dir / f"{safe}.md").write_text(content, encoding="utf-8")

        vis_counts: dict[str, int] = defaultdict(int)
        for f in funcs:
            vis_counts[f.get("visibility") or "unknown"] += 1
        macro_count = len(macros)

        return {
            "qn": module_qn,
            "files": files,
            "public": vis_counts.get("public", 0),
            "static": vis_counts.get("static", 0),
            "extern": vis_counts.get("extern", 0),
            "types": len(types),
            "total": len(funcs) + len(types),
            "macros": macro_count,
        }

    module_summaries: list[dict[str, Any]] = []
    sorted_modules = sorted(modules)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_write_l2, mq): mq for mq in sorted_modules}
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                logger.warning(f"L2 doc generation error: {exc}")
            else:
                module_summaries.append(fut.result())
    # Restore sorted order
    module_summaries.sort(key=lambda s: s["qn"])

    # ---- Generate L1: global index ----
    total_types = sum(len(m["types"]) for m in modules.values())
    index_content = _render_index(
        module_summaries, total_funcs, total_types,
        import_graph=dict(import_graph) if import_graph else None,
    )
    (api_dir / "index.md").write_text(index_content, encoding="utf-8")

    logger.info(
        f"API docs generated: {len(modules)} modules, "
        f"{total_funcs} functions, {total_types} types"
    )
    return {
        "module_count": len(modules),
        "func_count": total_funcs,
        "type_count": total_types,
    }
