"""Upward call-chain tracer.

Given a target function name, traces all callers upward through the code
knowledge graph using BFS, then rebuilds concrete paths via DFS.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_graph_builder.domains.core.search.graph_query import (
        GraphNode,
        GraphQueryService,
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NodeInfo:
    """Lightweight view of a graph node relevant to call-chain tracing."""

    qualified_name: str
    name: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class EdgeInfo:
    """Metadata about a single edge in a call path."""

    indirect: bool = False
    via_field: str | None = None


@dataclass
class CallPath:
    """An ordered path from an entry point down to the target function."""

    nodes: list[NodeInfo]  # entry_point -> ... -> target, ordered
    edges: list[EdgeInfo] = field(default_factory=list)  # len = len(nodes) - 1

    @property
    def depth(self) -> int:
        """Number of edges (hops) in this path."""
        return len(self.nodes) - 1

    @property
    def has_indirect(self) -> bool:
        """Whether any edge in this path is an indirect (function pointer) call."""
        return any(e.indirect for e in self.edges)


@dataclass
class SingleTraceResult:
    """Trace result for a single target function."""

    target: NodeInfo
    direct_callers: list[NodeInfo]
    entry_points: list[NodeInfo]
    paths: list[CallPath]
    max_depth_reached: bool
    truncated: bool


@dataclass
class TraceResult:
    """Complete result, may contain multiple matches for same-name functions."""

    results: list[SingleTraceResult]
    query_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph_node_to_info(node: GraphNode) -> NodeInfo:
    """Convert a full *GraphNode* to a lightweight *NodeInfo*."""
    return NodeInfo(
        qualified_name=node.qualified_name,
        name=node.name,
        path=node.path,
        start_line=node.start_line,
        end_line=node.end_line,
    )


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def trace_call_chain(
    query_service: GraphQueryService,
    target_function: str,
    max_depth: int = 10,
    paths_per_entry_point: int = 20,
) -> TraceResult:
    """Trace callers of *target_function* upward through the call graph.

    Parameters
    ----------
    query_service:
        A service implementing ``fetch_functions_by_name`` and
        ``fetch_callers``.
    target_function:
        Simple or qualified name of the function to trace.
    max_depth:
        Maximum BFS depth.  Nodes discovered at this depth that still have
        callers will be treated as entry points and
        ``max_depth_reached`` will be set.
    paths_per_entry_point:
        Maximum number of DFS paths to reconstruct per entry point.
        If exceeded, ``truncated`` is set on the result.

    Returns
    -------
    TraceResult
        One :class:`SingleTraceResult` per matched target node.

    Raises
    ------
    ValueError
        If no function matching *target_function* exists in the graph.
    """

    targets = query_service.fetch_functions_by_name(target_function)
    if not targets:
        raise ValueError(f"Function not found: {target_function}")

    single_results: list[SingleTraceResult] = []

    for target_node in targets:
        result = _trace_single_target(
            query_service,
            target_node,
            max_depth=max_depth,
            paths_per_entry_point=paths_per_entry_point,
        )
        single_results.append(result)

    return TraceResult(results=single_results, query_name=target_function)


# ---------------------------------------------------------------------------
# Per-target BFS + DFS
# ---------------------------------------------------------------------------


def _trace_single_target(
    query_service: GraphQueryService,
    target_node: GraphNode,
    *,
    max_depth: int,
    paths_per_entry_point: int,
) -> SingleTraceResult:
    """Run BFS upward from *target_node*, then reconstruct paths via DFS."""

    target_info = _graph_node_to_info(target_node)
    target_qn = target_node.qualified_name

    # Maps used during BFS
    visited: set[str] = {target_qn}
    parent_map: dict[str, list[str]] = {}  # child_qn -> [parent_qns]
    node_map: dict[str, NodeInfo] = {target_qn: target_info}
    # Edge properties: (parent_qn, child_qn) -> EdgeInfo
    edge_props: dict[tuple[str, str], EdgeInfo] = {}

    direct_callers: list[str] = []
    entry_points: list[str] = []
    max_depth_reached = False

    queue: deque[tuple[str, int]] = deque([(target_qn, 0)])

    while queue:
        current_qn, depth = queue.popleft()

        if depth >= max_depth:
            # Treat this node as an entry point; flag depth exceeded.
            if current_qn != target_qn:
                entry_points.append(current_qn)
            max_depth_reached = True
            continue

        # Use rel-props-aware query to capture indirect call metadata.
        callers_with_props = query_service.fetch_callers_with_rel_props(current_qn)

        for caller_node, rel_props in callers_with_props:
            caller_qn = caller_node.qualified_name

            # Record the parent relationship (even if already visited,
            # to capture all edges for path reconstruction).
            parent_map.setdefault(current_qn, []).append(caller_qn)

            # Store edge properties (caller -> current direction in call graph,
            # but in our parent_map it's child -> parent).
            edge_key = (caller_qn, current_qn)
            if edge_key not in edge_props:
                edge_props[edge_key] = EdgeInfo(
                    indirect=bool(rel_props.get("indirect")),
                    via_field=rel_props.get("via_field"),
                )

            if caller_qn in visited:
                continue

            visited.add(caller_qn)
            node_map[caller_qn] = _graph_node_to_info(caller_node)

            if depth == 0:
                direct_callers.append(caller_qn)

            queue.append((caller_qn, depth + 1))

        # If no callers were found for this node (and it's not the target
        # itself), it is an entry point.
        if not callers_with_props and current_qn != target_qn:
            entry_points.append(current_qn)

    # Deduplicate entry points while preserving order.
    seen_ep: set[str] = set()
    unique_eps: list[str] = []
    for ep in entry_points:
        if ep not in seen_ep:
            seen_ep.add(ep)
            unique_eps.append(ep)
    entry_points = unique_eps

    # ------------------------------------------------------------------
    # DFS path rebuild: entry_point -> ... -> target
    # ------------------------------------------------------------------

    # Build children_map (reverse of parent_map).
    # parent_map maps child -> parents (upward), so reversing gives
    # parent -> children (downward toward target).
    children_map: dict[str, list[str]] = {}
    for child_qn, parent_qns in parent_map.items():
        for p_qn in parent_qns:
            children_map.setdefault(p_qn, []).append(child_qn)

    all_paths: list[CallPath] = []
    truncated = False

    for ep_qn in entry_points:
        paths_from_ep = _dfs_paths(
            children_map,
            node_map,
            edge_props,
            start=ep_qn,
            target=target_qn,
            limit=paths_per_entry_point,
        )
        if len(paths_from_ep) >= paths_per_entry_point:
            truncated = True
        all_paths.extend(paths_from_ep)

    # Sort paths by depth ascending.
    all_paths.sort(key=lambda p: p.depth)

    return SingleTraceResult(
        target=target_info,
        direct_callers=[node_map[qn] for qn in direct_callers],
        entry_points=[node_map[qn] for qn in entry_points],
        paths=all_paths,
        max_depth_reached=max_depth_reached,
        truncated=truncated,
    )


def _dfs_paths(
    children_map: dict[str, list[str]],
    node_map: dict[str, NodeInfo],
    edge_props: dict[tuple[str, str], EdgeInfo],
    *,
    start: str,
    target: str,
    limit: int,
) -> list[CallPath]:
    """Enumerate paths from *start* to *target* via DFS.

    Returns at most *limit* paths, each with corresponding edge metadata.
    """

    results: list[CallPath] = []
    # Stack holds (current_qn, path_so_far)
    stack: list[tuple[str, list[str]]] = [(start, [start])]

    while stack and len(results) < limit:
        current, path = stack.pop()

        if current == target:
            nodes = [node_map[qn] for qn in path]
            # Build edge info for each hop in the path.
            edges: list[EdgeInfo] = []
            for i in range(len(path) - 1):
                key = (path[i], path[i + 1])
                edges.append(edge_props.get(key, EdgeInfo()))
            results.append(CallPath(nodes=nodes, edges=edges))
            continue

        for child_qn in children_map.get(current, []):
            if child_qn not in path:  # avoid cycles within a single path
                stack.append((child_qn, path + [child_qn]))

    return results
