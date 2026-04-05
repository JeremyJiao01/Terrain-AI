"""Generate Wiki pages (markdown) for call chain trace results."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from code_graph_builder.domains.upper.calltrace.tracer import (
    CallPath,
    NodeInfo,
    SingleTraceResult,
    TraceResult,
)
from code_graph_builder.domains.upper.calltrace.formatter import format_tree

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".go": "go",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".rs": "rust",
}


def _detect_lang(path: str | None) -> str:
    """Return a code-fence language tag based on file extension."""
    if path is None:
        return ""
    suffix = Path(path).suffix
    return _EXT_TO_LANG.get(suffix, "")


# ---------------------------------------------------------------------------
# Source snippet reader
# ---------------------------------------------------------------------------


def _read_source_snippet(repo_root: Path, node: NodeInfo) -> str | None:
    """Read the source code for a function from the repository.

    Returns ``None`` when the location information is missing or the file
    cannot be read.
    """
    if node.path is None or node.start_line is None:
        return None

    try:
        full_path = repo_root / node.path
        text = full_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        start = node.start_line - 1  # 0-based index
        end = node.end_line if node.end_line is not None else node.start_line + 50
        snippet_lines = lines[start:end]
        return "\n".join(snippet_lines)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Wiki page renderer
# ---------------------------------------------------------------------------


def _render_wiki_page(
    result: SingleTraceResult,
    repo_root: Path,
    repo_name: str,
) -> str:
    """Render a full markdown wiki page for a single trace result."""

    target = result.target
    direct_callers = result.direct_callers
    entry_points = result.entry_points
    paths = result.paths

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # --- Location helpers ---
    def _loc(node: NodeInfo) -> str:
        if node.path is None:
            return "unknown"
        if node.start_line is not None:
            return f"{node.path}:{node.start_line}"
        return node.path

    # --- Overview table ---
    indirect_note = (
        "本函数可能通过函数指针/回调被间接调用，见 Indirect Call Paths 段落"
    )
    overview = (
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Target Function | `{target.qualified_name}` |\n"
        f"| File | `{_loc(target)}` |\n"
        f"| Direct Callers | {len(direct_callers)} |\n"
        f"| Entry Points | {len(entry_points)} |\n"
        f"| Total Paths | {len(paths)} |\n"
        f"| ⚠️ Indirect Calls | {indirect_note} |"
    )

    # --- Call Tree ---
    tree_text = format_tree(result)

    # --- Entry Points Detail ---
    ep_sections: list[str] = []
    for i, ep in enumerate(entry_points, 1):
        lang = _detect_lang(ep.path)
        snippet = _read_source_snippet(repo_root, ep)
        source_block = (
            f"```{lang}\n{snippet}\n```" if snippet else "*Source not available.*"
        )
        ep_sections.append(
            f"### EP{i}: {ep.name} (`{_loc(ep)}`)\n"
            f"\n"
            f"**Source Code:**\n"
            f"\n"
            f"{source_block}\n"
            f"\n"
            f"**触发场景：** <!-- FILL: 该入口函数在什么业务场景下被调用？ -->\n"
            f"**触发条件：** <!-- FILL: 触发需要满足什么前置条件？ -->\n"
            f"**调用频率：** <!-- FILL: 高频/低频/仅异常时？ -->"
        )

    # --- Path Analysis ---
    path_sections: list[str] = []
    for i, cp in enumerate(paths, 1):
        if cp.nodes:
            ep_name = cp.nodes[0].name
            target_name = cp.nodes[-1].name
        else:
            ep_name = "?"
            target_name = target.name
        depth = len(cp.nodes)

        arrow_chain = " → ".join(n.name for n in cp.nodes) if cp.nodes else "?"
        header = f"### Path {i}: {arrow_chain} (depth: {depth})\n"

        rows: list[str] = []
        for j, node in enumerate(cp.nodes, 1):
            rows.append(
                f"| {j} | `{node.name}()` | `{_loc(node)}` "
                f"| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |"
            )
        table = (
            "| # | Function | File | 触发条件 | 关键参数 | 日志输出 |\n"
            "|---|----------|------|----------|----------|----------|\n"
            + "\n".join(rows)
        )

        path_sections.append(
            f"{header}\n"
            f"{table}\n"
            f"\n"
            f"**路径摘要：** <!-- FILL -->\n"
            f"**异常分支：** <!-- FILL -->"
        )

    # --- Assemble page ---
    page = (
        f"# Call Chain Trace: {target.name}\n"
        f"\n"
        f"> Generated: {timestamp} | Repository: {repo_name}\n"
        f"> Status: 🔲 待填充\n"
        f"\n"
        f"## Overview\n"
        f"\n"
        f"{overview}\n"
        f"\n"
        f"## Call Tree\n"
        f"\n"
        f"```\n"
        f"{tree_text}\n"
        f"```\n"
        f"\n"
        f"## Entry Points Detail\n"
        f"\n"
        + "\n\n".join(ep_sections)
        + "\n"
        f"\n"
        f"## Path Analysis\n"
        f"\n"
        + "\n\n".join(path_sections)
        + "\n"
        f"\n"
        f"## Indirect Call Paths (Function Pointer / Callback)\n"
        f"\n"
        f"| 注册函数 | 注册文件 | 结构体/字段 | 间接调用点 | 调用文件 |\n"
        f"|----------|----------|-------------|-----------|----------|\n"
        f"| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |\n"
        f"\n"
        f"**注册模式描述：** <!-- FILL -->\n"
        f"\n"
        f"## Log Fingerprint\n"
        f"\n"
        f"| 日志特征 | 对应路径 | 对应函数 | 备注 |\n"
        f"|----------|----------|----------|------|\n"
        f"| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |\n"
        f"\n"
        f"## Investigation Notes\n"
        f"\n"
        f"<!-- FILL -->\n"
    )

    return page


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_wiki_pages(
    result: TraceResult,
    artifact_dir: Path,
    repo_root: Path,
    repo_name: str,
) -> list[Path]:
    """Write wiki pages for every :class:`SingleTraceResult` in *result*.

    Returns the list of file paths that were written.
    """
    output_dir = artifact_dir / "wiki" / "call-traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Detect name collisions so we can add a hash suffix when needed.
    name_counts: dict[str, int] = {}
    for single in result.results:
        name = single.target.name
        name_counts[name] = name_counts.get(name, 0) + 1

    written: list[Path] = []
    for single in result.results:
        content = _render_wiki_page(single, repo_root, repo_name)

        name = single.target.name
        if name_counts[name] > 1:
            hash8 = hashlib.md5(
                single.target.qualified_name.encode("utf-8")
            ).hexdigest()[:8]
            filename = f"trace-{name}-{hash8}.md"
        else:
            filename = f"trace-{name}.md"

        dest = output_dir / filename
        dest.write_text(content, encoding="utf-8")
        written.append(dest)

    return written
