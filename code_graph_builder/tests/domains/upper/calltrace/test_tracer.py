"""Unit tests for the call-chain tracer."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from code_graph_builder.domains.core.search.graph_query import GraphNode
from code_graph_builder.domains.upper.calltrace.tracer import (
    trace_call_chain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: int,
    qn: str,
    name: str,
    path: str = "test.go",
    start: int = 10,
    end: int = 20,
) -> GraphNode:
    """Return a minimal GraphNode for testing."""
    return GraphNode(
        node_id=node_id,
        qualified_name=qn,
        name=name,
        type="Function",
        path=path,
        start_line=start,
        end_line=end,
        docstring=None,
        properties=None,
    )


def _mock_query_service(
    targets: list[GraphNode],
    callers_map: dict[str, list[GraphNode]],
) -> MagicMock:
    """Build a mock GraphQueryService.

    Parameters
    ----------
    targets:
        Nodes returned by ``fetch_functions_by_name``.
    callers_map:
        Mapping from qualified_name to the list of GraphNodes returned
        by ``fetch_callers(qualified_name)``.
    """
    svc = MagicMock()
    svc.fetch_functions_by_name.return_value = targets
    svc.fetch_callers.side_effect = lambda qn: callers_map.get(qn, [])
    return svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_layer():
    """Target has 2 direct callers, no further callers."""
    target = _make_node(1, "pkg.target", "target")
    caller_a = _make_node(2, "pkg.callerA", "callerA")
    caller_b = _make_node(3, "pkg.callerB", "callerB")

    svc = _mock_query_service(
        targets=[target],
        callers_map={"pkg.target": [caller_a, caller_b]},
    )

    result = trace_call_chain(svc, "target")

    assert len(result.results) == 1
    single = result.results[0]

    assert len(single.direct_callers) == 2
    assert {c.qualified_name for c in single.direct_callers} == {
        "pkg.callerA",
        "pkg.callerB",
    }
    # callerA and callerB have no further callers -> they are entry points
    assert len(single.entry_points) == 2
    assert len(single.paths) == 2


def test_multi_layer():
    """A calls B calls target. Verify depth=2 path exists."""
    target = _make_node(1, "pkg.target", "target")
    b = _make_node(2, "pkg.B", "B")
    a = _make_node(3, "pkg.A", "A")

    svc = _mock_query_service(
        targets=[target],
        callers_map={
            "pkg.target": [b],
            "pkg.B": [a],
        },
    )

    result = trace_call_chain(svc, "target")
    single = result.results[0]

    assert len(single.entry_points) == 1
    assert single.entry_points[0].qualified_name == "pkg.A"

    # There should be a path A -> B -> target with depth 2
    assert any(p.depth == 2 for p in single.paths)


def test_branching():
    """Two separate entry points both reach target through different paths."""
    target = _make_node(1, "pkg.target", "target")
    mid = _make_node(2, "pkg.mid", "mid")
    ep1 = _make_node(3, "pkg.ep1", "ep1")
    ep2 = _make_node(4, "pkg.ep2", "ep2")

    svc = _mock_query_service(
        targets=[target],
        callers_map={
            "pkg.target": [mid, ep2],
            "pkg.mid": [ep1],
        },
    )

    result = trace_call_chain(svc, "target")
    single = result.results[0]

    ep_names = {ep.qualified_name for ep in single.entry_points}
    assert ep_names == {"pkg.ep1", "pkg.ep2"}


def test_cycle():
    """A calls B calls A (cycle). Should not infinite loop."""
    target = _make_node(1, "pkg.target", "target")
    a = _make_node(2, "pkg.A", "A")
    b = _make_node(3, "pkg.B", "B")

    svc = _mock_query_service(
        targets=[target],
        callers_map={
            "pkg.target": [a],
            "pkg.A": [b],
            "pkg.B": [a],  # cycle: B -> A -> B
        },
    )

    # Should complete without hanging
    result = trace_call_chain(svc, "target")
    single = result.results[0]

    # Both A and B should be visited; B has callers (A, which is visited)
    # so B is NOT an entry point. But A's caller B is visited, so the BFS
    # won't add B again. B's only caller is A (already visited). So B has
    # no *new* callers at its turn -> depends on BFS ordering.
    # The key assertion: it terminates and produces a result.
    assert single.target.qualified_name == "pkg.target"


def test_target_not_found():
    """fetch_functions_by_name returns empty -> ValueError."""
    svc = _mock_query_service(targets=[], callers_map={})

    with pytest.raises(ValueError, match="Function not found"):
        trace_call_chain(svc, "nonexistent")


def test_no_callers():
    """Target exists but has no callers."""
    target = _make_node(1, "pkg.target", "target")
    svc = _mock_query_service(targets=[target], callers_map={})

    result = trace_call_chain(svc, "target")
    single = result.results[0]

    assert single.direct_callers == []
    assert single.entry_points == []
    assert single.paths == []


def test_max_depth():
    """Chain deeper than max_depth=2. Verify max_depth_reached=True."""
    target = _make_node(1, "pkg.target", "target")
    a = _make_node(2, "pkg.A", "A")
    b = _make_node(3, "pkg.B", "B")
    c = _make_node(4, "pkg.C", "C")

    svc = _mock_query_service(
        targets=[target],
        callers_map={
            "pkg.target": [a],
            "pkg.A": [b],
            "pkg.B": [c],
        },
    )

    result = trace_call_chain(svc, "target", max_depth=2)
    single = result.results[0]

    assert single.max_depth_reached is True


def test_same_name_multiple_matches():
    """fetch_functions_by_name returns 2 nodes -> 2 SingleTraceResults."""
    target1 = _make_node(1, "pkg1.save", "save", path="pkg1/save.go")
    target2 = _make_node(2, "pkg2.save", "save", path="pkg2/save.go")

    svc = _mock_query_service(
        targets=[target1, target2],
        callers_map={},
    )

    result = trace_call_chain(svc, "save")

    assert len(result.results) == 2
    qns = {r.target.qualified_name for r in result.results}
    assert qns == {"pkg1.save", "pkg2.save"}


def test_paths_per_entry_point_limit():
    """Set paths_per_entry_point=1 with branching that produces 2 paths.

    Verify truncated=True on the result.
    """
    target = _make_node(1, "pkg.target", "target")
    mid_a = _make_node(2, "pkg.midA", "midA")
    mid_b = _make_node(3, "pkg.midB", "midB")
    ep = _make_node(4, "pkg.ep", "ep")

    # ep -> midA -> target  AND  ep -> midB -> target  (2 paths from ep)
    svc = _mock_query_service(
        targets=[target],
        callers_map={
            "pkg.target": [mid_a, mid_b],
            "pkg.midA": [ep],
            "pkg.midB": [ep],
        },
    )

    result = trace_call_chain(svc, "target", paths_per_entry_point=1)
    single = result.results[0]

    assert single.truncated is True
