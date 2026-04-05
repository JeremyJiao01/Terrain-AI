"""Format call chain trace results as tree text."""

from __future__ import annotations

from collections import defaultdict

from code_graph_builder.domains.upper.calltrace.tracer import (
    CallPath,
    NodeInfo,
    SingleTraceResult,
    TraceResult,
)

_SEP_MAJOR = "=" * 60
_SEP_MINOR = "-" * 60


def _format_path_line(
    name: str, location: str, indent: int, column_width: int
) -> str:
    """Format a single line of the call tree.

    Parameters
    ----------
    name:
        Function name (without trailing ``()``).
    location:
        File location string, e.g. ``"pkg/log/save.go:30"``.
    indent:
        Nesting depth (0 = root, no prefix).
    column_width:
        Total line width to pad to.
    """
    if indent == 0:
        left = f"{name}()"
    else:
        prefix = "    " * indent + "└── "
        left = f"{prefix}{name}()"

    if not location:
        return left

    padding = column_width - len(left) - len(location)
    if padding < 1:
        padding = 1
    return f"{left}{' ' * padding}{location}"


def _node_location(node: NodeInfo) -> str:
    """Return ``path:start_line`` or empty string when path is *None*."""
    if node.path is None:
        return ""
    if node.start_line is not None:
        return f"{node.path}:{node.start_line}"
    return node.path


def format_tree(result: SingleTraceResult, column_width: int = 80) -> str:
    """Format a single trace result as a tree-text report."""
    lines: list[str] = []

    target = result.target
    target_loc = _node_location(target)

    # --- Header ---
    lines.append(_SEP_MAJOR)
    lines.append(f"  Call Chain Trace: {target.name}")
    lines.append(_SEP_MAJOR)
    lines.append("")

    # --- Summary ---
    lines.append(f"Target: {target.name}")
    if target_loc:
        lines.append(f"  File: {target_loc}")
    lines.append(f"  Direct callers: {len(result.direct_callers)}")
    lines.append(f"  Entry points: {len(result.entry_points)}")
    lines.append(f"  Total paths: {len(result.paths)}")
    lines.append("")

    # Group paths by entry point (first node in each path).
    ep_groups: dict[str, list[CallPath]] = defaultdict(list)
    ep_order: list[str] = []
    for path in result.paths:
        if not path.nodes:
            continue
        ep_key = path.nodes[0].qualified_name if hasattr(path.nodes[0], "qualified_name") else path.nodes[0].name
        if ep_key not in ep_groups:
            ep_order.append(ep_key)
        ep_groups[ep_key].append(path)

    # Sort paths within each group by depth ascending.
    for key in ep_groups:
        ep_groups[key].sort(key=lambda p: p.depth)

    for ep_idx, ep_key in enumerate(ep_order, start=1):
        paths = ep_groups[ep_key]
        ep_node = paths[0].nodes[0]
        ep_loc = _node_location(ep_node)

        lines.append(_SEP_MINOR)
        ep_header = f"  Entry Point {ep_idx}: {ep_node.name}"
        if ep_loc:
            ep_header += f" ({ep_loc})"
        lines.append(ep_header)
        lines.append(_SEP_MINOR)
        lines.append("")

        for path in paths:
            for depth_idx, node in enumerate(path.nodes):
                loc = _node_location(node)
                lines.append(
                    _format_path_line(node.name, loc, depth_idx, column_width)
                )
            lines.append("")

        if result.truncated:
            lines.append("... and more paths from this entry point (truncated)")
            lines.append("")

    return "\n".join(lines)


def format_trace_result(result: TraceResult) -> str:
    """Format a full *TraceResult* (potentially multiple targets)."""
    if not result.results:
        return f"No results found for: {result.query_name}"

    separator = f"\n\n{_SEP_MAJOR}\n\n"
    return separator.join(format_tree(r) for r in result.results)
