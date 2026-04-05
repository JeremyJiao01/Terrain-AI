"""Unit tests for the call-chain formatter."""

from __future__ import annotations

from code_graph_builder.domains.upper.calltrace.tracer import (
    CallPath,
    NodeInfo,
    SingleTraceResult,
    TraceResult,
)
from code_graph_builder.domains.upper.calltrace.formatter import (
    format_tree,
    format_trace_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(qn: str, name: str, path: str = "pkg/main.go", start: int = 10) -> NodeInfo:
    return NodeInfo(
        qualified_name=qn,
        name=name,
        path=path,
        start_line=start,
        end_line=start + 10,
    )


def _single_result(
    target: NodeInfo,
    direct_callers: list[NodeInfo] | None = None,
    entry_points: list[NodeInfo] | None = None,
    paths: list[CallPath] | None = None,
    truncated: bool = False,
) -> SingleTraceResult:
    return SingleTraceResult(
        target=target,
        direct_callers=direct_callers or [],
        entry_points=entry_points or [],
        paths=paths or [],
        max_depth_reached=False,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_format_tree_basic():
    """Single EP, single path. Output contains target name, EP name, tree chars."""
    target = _node("pkg.save", "save")
    ep = _node("pkg.main", "main", start=1)
    path = CallPath(nodes=[ep, target])

    result = _single_result(
        target=target,
        direct_callers=[ep],
        entry_points=[ep],
        paths=[path],
    )

    output = format_tree(result)

    assert "save" in output
    assert "main" in output
    assert "\u2514\u2500\u2500" in output  # └──


def test_format_tree_multiple_eps():
    """Two entry points. Check both EP headers appear."""
    target = _node("pkg.save", "save")
    ep1 = _node("pkg.handler1", "handler1", start=1)
    ep2 = _node("pkg.handler2", "handler2", start=20)
    path1 = CallPath(nodes=[ep1, target])
    path2 = CallPath(nodes=[ep2, target])

    result = _single_result(
        target=target,
        direct_callers=[ep1, ep2],
        entry_points=[ep1, ep2],
        paths=[path1, path2],
    )

    output = format_tree(result)

    assert "Entry Point 1" in output
    assert "Entry Point 2" in output
    assert "handler1" in output
    assert "handler2" in output


def test_format_tree_truncated():
    """result.truncated=True. Check '... and' appears in output."""
    target = _node("pkg.save", "save")
    ep = _node("pkg.main", "main", start=1)
    path = CallPath(nodes=[ep, target])

    result = _single_result(
        target=target,
        direct_callers=[ep],
        entry_points=[ep],
        paths=[path],
        truncated=True,
    )

    output = format_tree(result)

    assert "... and" in output


def test_format_trace_result_empty():
    """Empty results list. Check 'No results found' message."""
    result = TraceResult(results=[], query_name="missing_fn")

    output = format_trace_result(result)

    assert "No results found" in output
    assert "missing_fn" in output


def test_format_trace_result_multiple():
    """Two SingleTraceResults. Check separator exists."""
    target1 = _node("pkg1.save", "save", path="pkg1/save.go")
    target2 = _node("pkg2.save", "save", path="pkg2/save.go")

    r1 = _single_result(target=target1)
    r2 = _single_result(target=target2)

    result = TraceResult(results=[r1, r2], query_name="save")

    output = format_trace_result(result)

    # The separator is a line of '=' characters
    assert "=" * 60 in output


def test_alignment():
    """Verify file locations are right-aligned (padded spaces in lines)."""
    target = _node("pkg.save", "save", path="pkg/log/save.go", start=30)
    ep = _node("pkg.main", "main", path="cmd/main.go", start=1)
    path = CallPath(nodes=[ep, target])

    result = _single_result(
        target=target,
        direct_callers=[ep],
        entry_points=[ep],
        paths=[path],
    )

    output = format_tree(result, column_width=80)

    # Find lines that contain both function name and file location.
    # These lines should have padding spaces between them.
    for line in output.splitlines():
        if "main()" in line and "cmd/main.go" in line:
            # There should be multiple consecutive spaces for alignment
            assert "  " in line
            break
    else:
        pytest.fail("Could not find aligned line with main() and its location")
