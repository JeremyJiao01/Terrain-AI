"""Unit tests for the call-chain wiki writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_graph_builder.domains.upper.calltrace.tracer import (
    CallPath,
    NodeInfo,
    SingleTraceResult,
    TraceResult,
)
from code_graph_builder.domains.upper.calltrace.wiki_writer import (
    _read_source_snippet,
    _render_wiki_page,
    write_wiki_pages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(
    qn: str,
    name: str,
    path: str | None = "pkg/main.go",
    start: int = 10,
    end: int = 20,
) -> NodeInfo:
    return NodeInfo(
        qualified_name=qn,
        name=name,
        path=path,
        start_line=start,
        end_line=end,
    )


def _minimal_single_result(
    target: NodeInfo | None = None,
    entry_points: list[NodeInfo] | None = None,
    paths: list[CallPath] | None = None,
) -> SingleTraceResult:
    if target is None:
        target = _node("pkg.save", "save")
    return SingleTraceResult(
        target=target,
        direct_callers=entry_points or [],
        entry_points=entry_points or [],
        paths=paths or [],
        max_depth_reached=False,
        truncated=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_render_wiki_page_sections(tmp_path: Path):
    """Rendered page contains all required section headers."""
    ep = _node("pkg.main", "main", start=1, end=5)
    target = _node("pkg.save", "save")
    path = CallPath(nodes=[ep, target])

    result = _minimal_single_result(
        target=target,
        entry_points=[ep],
        paths=[path],
    )

    page = _render_wiki_page(result, repo_root=tmp_path, repo_name="test-repo")

    required_sections = [
        "## Overview",
        "## Call Tree",
        "## Entry Points Detail",
        "## Path Analysis",
        "## Indirect Call Paths",
        "## Log Fingerprint",
        "## Investigation Notes",
    ]
    for section in required_sections:
        assert section in page, f"Missing section: {section}"


def test_fill_markers(tmp_path: Path):
    """Verify <!-- FILL markers appear in the output."""
    ep = _node("pkg.main", "main", start=1, end=5)
    target = _node("pkg.save", "save")
    path = CallPath(nodes=[ep, target])

    result = _minimal_single_result(
        target=target,
        entry_points=[ep],
        paths=[path],
    )

    page = _render_wiki_page(result, repo_root=tmp_path, repo_name="test-repo")

    assert "<!-- FILL" in page


def test_indirect_call_paths_section(tmp_path: Path):
    """Verify the Indirect Call Paths table exists."""
    result = _minimal_single_result()

    page = _render_wiki_page(result, repo_root=tmp_path, repo_name="test-repo")

    assert "Indirect Call Paths" in page
    # Table header row
    assert "注册函数" in page
    assert "间接调用点" in page


def test_write_wiki_pages_creates_files(tmp_path: Path):
    """write_wiki_pages creates files in the correct directory."""
    target = _node("pkg.save", "save")
    single = _minimal_single_result(target=target)
    trace = TraceResult(results=[single], query_name="save")

    written = write_wiki_pages(
        result=trace,
        artifact_dir=tmp_path,
        repo_root=tmp_path,
        repo_name="test-repo",
    )

    assert len(written) == 1
    assert written[0].exists()
    assert written[0].name == "trace-save.md"
    assert "wiki/call-traces" in str(written[0])


def test_write_wiki_pages_hash_suffix(tmp_path: Path):
    """Multiple results with same name get hash suffix in filename."""
    target1 = _node("pkg1.save", "save", path="pkg1/save.go")
    target2 = _node("pkg2.save", "save", path="pkg2/save.go")

    single1 = _minimal_single_result(target=target1)
    single2 = _minimal_single_result(target=target2)

    trace = TraceResult(results=[single1, single2], query_name="save")

    written = write_wiki_pages(
        result=trace,
        artifact_dir=tmp_path,
        repo_root=tmp_path,
        repo_name="test-repo",
    )

    assert len(written) == 2
    filenames = {p.name for p in written}
    # Both should have hash suffixes since they share the name "save"
    for fname in filenames:
        assert fname.startswith("trace-save-")
        assert len(fname) > len("trace-save-.md")  # has hash portion


def test_source_snippet_missing_path(tmp_path: Path):
    """NodeInfo with path=None returns None from _read_source_snippet."""
    node = _node("pkg.fn", "fn", path=None, start=1, end=5)

    result = _read_source_snippet(tmp_path, node)

    assert result is None


def test_source_snippet_reads_file(tmp_path: Path):
    """Create a temp file, verify _read_source_snippet reads correct lines."""
    src_file = tmp_path / "pkg" / "main.go"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"line {i}" for i in range(1, 31)]
    src_file.write_text("\n".join(lines), encoding="utf-8")

    node = _node("pkg.fn", "fn", path="pkg/main.go", start=5, end=8)

    snippet = _read_source_snippet(tmp_path, node)

    assert snippet is not None
    # Lines 5-8 (1-based) -> 0-based index 4:8
    assert "line 5" in snippet
    assert "line 8" in snippet
    assert "line 9" not in snippet
