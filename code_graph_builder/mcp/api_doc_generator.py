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


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _unpack_row(row: dict[str, Any]) -> list[Any]:
    """Normalise a Kùzu result row to a flat list."""
    raw = row.get("result", row)
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


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

        callees_of[caller_qn].append({
            "qn": callee_qn,
            "path": callee_path,
            "start_line": callee_start,
        })
        callers_of[callee_qn].append({
            "qn": caller_qn,
            "path": callee_path,
            "start_line": callee_start,
        })

    return callers_of, callees_of


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
) -> str:
    """Render L3 detail page for a single function."""
    lines: list[str] = []
    qn = func["qn"]
    lines.append(f"# {qn}")
    lines.append("")
    lines.append(f"- **Signature**: `{func.get('signature') or func['name']}`")
    if func.get("return_type"):
        lines.append(f"- **Return**: `{func['return_type']}`")
    lines.append(f"- **Visibility**: {func.get('visibility') or 'unknown'}")
    loc_path = func.get("path") or ""
    start = func.get("start_line") or "?"
    end = func.get("end_line") or "?"
    lines.append(f"- **Location**: {loc_path}:{start}-{end}")
    lines.append(f"- **Module**: {func.get('module_qn', '')}")
    lines.append("")

    # Docstring
    doc = func.get("docstring")
    if doc:
        lines.append("## Description")
        lines.append("")
        lines.append(doc.strip())
        lines.append("")

    # Callers
    lines.append(f"## Called by ({len(callers)})")
    lines.append("")
    if callers:
        for c in callers:
            loc = ""
            if c.get("path") and c.get("start_line"):
                loc = f" — {c['path']}:{c['start_line']}"
            lines.append(f"- `{c['qn']}`{loc}")
    else:
        lines.append("*(no callers found)*")
    lines.append("")

    # Callees
    lines.append(f"## Calls ({len(callees)})")
    lines.append("")
    if callees:
        for c in callees:
            loc = ""
            if c.get("path") and c.get("start_line"):
                loc = f" — {c['path']}:{c['start_line']}"
            lines.append(f"- `{c['qn']}`{loc}")
    else:
        lines.append("*(no outgoing calls found)*")
    lines.append("")

    return "\n".join(lines)


def _render_module_page(
    module_qn: str,
    files: list[str],
    funcs: list[dict[str, Any]],
    types: list[dict[str, Any]],
) -> str:
    """Render L2 module index page."""
    lines: list[str] = []
    lines.append(f"# {module_qn}")
    lines.append("")
    lines.append(f"**Files**: {', '.join(files)}")
    lines.append("")

    # Group functions by visibility
    by_vis: dict[str, list[dict]] = defaultdict(list)
    for f in funcs:
        by_vis[f.get("visibility") or "unknown"].append(f)

    vis_order = ["public", "extern", "static", "unknown"]
    vis_labels = {
        "public": "Public API (declared in header)",
        "extern": "Extern (no header declaration)",
        "static": "Static (file-local)",
        "unknown": "Other",
    }

    for vis in vis_order:
        group = by_vis.get(vis)
        if not group:
            continue
        lines.append(f"## {vis_labels.get(vis, vis)} ({len(group)})")
        lines.append("")
        lines.append("| Function | Signature | Lines |")
        lines.append("|----------|-----------|-------|")
        for f in group:
            safe = _sanitise_filename(f["qn"])
            sig = f.get("signature") or f["name"]
            loc = f"{f.get('path', '')}:{f.get('start_line', '?')}-{f.get('end_line', '?')}"
            lines.append(f"| [{f['name']}](../funcs/{safe}.md) | `{sig}` | {loc} |")
        lines.append("")

    # Types
    if types:
        lines.append(f"## Types ({len(types)})")
        lines.append("")
        lines.append("| Name | Kind | Signature |")
        lines.append("|------|------|-----------|")
        for t in types:
            lines.append(
                f"| {t.get('name', '?')} | {t.get('kind', '?')} "
                f"| `{t.get('signature', '')}` |"
            )
        lines.append("")

    return "\n".join(lines)


def _render_index(
    module_summaries: list[dict[str, Any]],
    total_funcs: int,
    total_types: int,
) -> str:
    """Render L1 global index page."""
    lines: list[str] = []
    lines.append("# API Documentation Index")
    lines.append("")
    lines.append(f"Total: {len(module_summaries)} modules, "
                 f"{total_funcs} functions, {total_types} types")
    lines.append("")
    lines.append("| Module | Files | Public | Static | Extern | Types | Total |")
    lines.append("|--------|-------|--------|--------|--------|-------|-------|")

    for m in module_summaries:
        safe = _sanitise_filename(m["qn"])
        files = ", ".join(m["files"])
        lines.append(
            f"| [{m['qn']}](modules/{safe}.md) | {files} "
            f"| {m['public']} | {m['static']} | {m['extern']} "
            f"| {m['types']} | {m['total']} |"
        )
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
) -> dict[str, Any]:
    """Generate hierarchical API documentation from pre-fetched graph data.

    Args:
        func_rows: Rows from fetch_all_functions_for_docs query.
        type_rows: Rows from fetch_all_types_for_docs query.
        call_rows: Rows from fetch_all_calls query.
        output_dir: Directory to write api_docs/ into.

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
        lambda: {"files": set(), "funcs": [], "types": []}
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
        func = {
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
            "path": r[11] if len(r) > 11 else module_path,
        }
        modules[module_qn]["files"].add(module_path)
        modules[module_qn]["funcs"].append(func)

    for row in type_rows:
        r = _unpack_row(row)
        if len(r) < 6:
            continue
        module_qn = r[0] or "unknown"
        type_info = {
            "name": r[1],
            "kind": r[2],
            "signature": r[3],
            "members": r[4] if len(r) > 4 else None,
            "start_line": r[4 if len(r) <= 5 else 5],
            "end_line": r[5 if len(r) <= 6 else 6],
        }
        modules[module_qn]["types"].append(type_info)

    # ---- Collect all known files per module ----
    # Since .c and .h share module_qn, we need to discover both file paths.
    # The func rows carry module_path (last-written, typically .c).
    # We also query for any .h counterpart by checking the func paths.
    for mod_data in modules.values():
        paths = set()
        for f in mod_data["funcs"]:
            p = f.get("path") or ""
            if p:
                paths.add(Path(p).name)
        mod_data["files"].update(paths)
        # Remove empty strings
        mod_data["files"].discard("")

    # ---- Generate L3: per-function detail pages ----
    total_funcs = 0
    for mod_data in modules.values():
        for func in mod_data["funcs"]:
            qn = func["qn"]
            if not qn:
                continue
            content = _render_func_detail(
                func,
                callers=callers_of.get(qn, []),
                callees=callees_of.get(qn, []),
            )
            safe = _sanitise_filename(qn)
            (funcs_dir / f"{safe}.md").write_text(content, encoding="utf-8")
            total_funcs += 1

    # ---- Generate L2: per-module pages ----
    module_summaries: list[dict[str, Any]] = []
    for module_qn in sorted(modules):
        mod_data = modules[module_qn]
        funcs = mod_data["funcs"]
        types = mod_data["types"]
        files = sorted(mod_data["files"])

        content = _render_module_page(module_qn, files, funcs, types)
        safe = _sanitise_filename(module_qn)
        (modules_dir / f"{safe}.md").write_text(content, encoding="utf-8")

        # Summary stats
        vis_counts = defaultdict(int)
        for f in funcs:
            vis_counts[f.get("visibility") or "unknown"] += 1

        module_summaries.append({
            "qn": module_qn,
            "files": files,
            "public": vis_counts.get("public", 0),
            "static": vis_counts.get("static", 0),
            "extern": vis_counts.get("extern", 0),
            "types": len(types),
            "total": len(funcs) + len(types),
        })

    # ---- Generate L1: global index ----
    total_types = sum(len(m["types"]) for m in modules.values())
    index_content = _render_index(module_summaries, total_funcs, total_types)
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
