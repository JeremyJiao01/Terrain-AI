"""Tests for the ``extract_predicates`` MCP tool (slice 1/3 of JER-47).

Slice 1 MVP returns only the predicate skeleton — ``kind``, ``location``,
``expression`` and ``nesting_path``. Later slices add ``symbols_referenced``,
``guarded_block`` etc.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _run(coro):
    return asyncio.run(coro)


def _make_registry(tmp_path: Path, repo: Path):
    """Build an MCPToolsRegistry pointed at *repo* without running the indexer.

    ``extract_predicates`` re-parses C source on demand; it does not need a
    populated graph.
    """
    from terrain.entrypoints.mcp.tools import MCPToolsRegistry

    ws = tmp_path / "workspace"
    ws.mkdir()
    registry = MCPToolsRegistry(ws)
    registry._active_repo_path = repo
    registry._active_artifact_dir = tmp_path / "artifact"
    registry._active_artifact_dir.mkdir()
    registry._db_path = registry._active_artifact_dir / "graph.db"
    return registry


def _call(registry, tool_name: str, args: dict | None = None):
    handler = registry.get_handler(tool_name)
    assert handler is not None, f"Tool '{tool_name}' not registered"
    result = _run(handler(**(args or {})))
    # Round-trip through JSON to ensure the payload is fully serialisable.
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tool_appears_in_tools_list(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        names = [t.name for t in reg.tools()]
        assert "extract_predicates" in names

    def test_handler_dispatches(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        assert reg.get_handler("extract_predicates") is not None


# ---------------------------------------------------------------------------
# Happy-path extraction
# ---------------------------------------------------------------------------


class TestBasicExtraction:
    def test_nested_if_three_levels(self, tmp_path):
        """Three-level nested if — each inner predicate carries its outer
        headers in ``nesting_path``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "alarm.c").write_text(
            """\
int AlarmCheck_DCI(int grid, int filt, int cnt) {
    if (grid) {
        if (filt > 10) {
            if (cnt > 3) {
                return 1;
            }
        }
    }
    return 0;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "AlarmCheck_DCI"})

        assert result["success"] is True
        assert result["function"] == "AlarmCheck_DCI"
        kinds = [p["kind"] for p in result["predicates"]]
        assert kinds == ["if", "if", "if"]
        expressions = [p["expression"] for p in result["predicates"]]
        assert expressions == ["grid", "filt > 10", "cnt > 3"]
        # nesting_path: outermost first, each predicate's own entry is NOT
        # included in its own path.
        assert result["predicates"][0]["nesting_path"] == []
        assert result["predicates"][1]["nesting_path"] == ["if (grid)"]
        assert result["predicates"][2]["nesting_path"] == ["if (grid)", "if (filt > 10)"]
        # locations point to the correct lines
        assert result["predicates"][0]["location"] == "alarm.c:2"
        assert result["predicates"][1]["location"] == "alarm.c:3"
        assert result["predicates"][2]["location"] == "alarm.c:4"

    def test_switch_case(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "sw.c").write_text(
            """\
int dispatch(int code) {
    switch (code) {
        case 1:
            return 10;
        case 2:
            return 20;
        default:
            return 0;
    }
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "dispatch"})

        assert result["success"] is True
        kinds = [p["kind"] for p in result["predicates"]]
        # switch itself is not captured — only its cases (slice 1 MVP).
        assert kinds == ["switch_case", "switch_case", "switch_case"]
        expressions = [p["expression"] for p in result["predicates"]]
        assert expressions == ["case 1", "case 2", "default"]
        for p in result["predicates"]:
            assert p["nesting_path"] == ["switch (code)"]

    def test_ternary(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "t.c").write_text(
            """\
int pick(int x) {
    int y = (x > 0) ? 1 : -1;
    return y;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "pick"})

        assert result["success"] is True
        assert len(result["predicates"]) == 1
        p = result["predicates"][0]
        assert p["kind"] == "ternary"
        # Condition text may or may not keep the outer parens depending on the
        # tree-sitter grammar shape — accept both forms.
        assert p["expression"].replace(" ", "") in ("x>0", "(x>0)")
        assert p["nesting_path"] == []

    def test_else_if_classification(self, tmp_path):
        """``else if`` must be reported as kind='else_if', not 'if'."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "e.c").write_text(
            """\
int grade(int s) {
    if (s >= 90) {
        return 1;
    } else if (s >= 80) {
        return 2;
    } else if (s >= 70) {
        return 3;
    }
    return 0;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "grade"})

        assert result["success"] is True
        kinds = [p["kind"] for p in result["predicates"]]
        assert kinds == ["if", "else_if", "else_if"]

    def test_for_while_do_while(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "loops.c").write_text(
            """\
int sweep(int n) {
    for (int i = 0; i < n; i++) {
        while (n > 0) {
            n--;
        }
    }
    do {
        n++;
    } while (n < 5);
    return n;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "sweep"})

        assert result["success"] is True
        kinds = [p["kind"] for p in result["predicates"]]
        assert kinds == ["for", "while", "do_while"]
        # for predicate's own expression should be the condition only
        for_pred = result["predicates"][0]
        assert for_pred["expression"] == "i < n"
        # while is nested inside for
        assert result["predicates"][1]["nesting_path"][0].startswith("for (int i = 0;")
        # do_while is top-level — no enclosing predicate
        assert result["predicates"][2]["nesting_path"] == []
        assert result["predicates"][2]["expression"] == "n < 5"


# ---------------------------------------------------------------------------
# Deep nesting — march of nines
# ---------------------------------------------------------------------------


class TestDeepNesting:
    def test_five_level_nesting(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "deep.c").write_text(
            """\
int deep(int a, int b, int c, int d, int e) {
    if (a) {
        if (b) {
            if (c) {
                if (d) {
                    if (e) {
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "deep"})

        assert result["success"] is True
        preds = result["predicates"]
        assert len(preds) == 5
        for i, p in enumerate(preds):
            assert p["kind"] == "if"
            assert p["expression"] == ["a", "b", "c", "d", "e"][i]
            assert len(p["nesting_path"]) == i


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_function_not_found(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text("int bar(void) { return 0; }\n", encoding="utf-8")

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "missing"})

        assert result["success"] is False
        assert result["error"] == "function not found"
        assert result["function"] == "missing"

    def test_ambiguous_function(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            "int foo(void) { if (1) { return 1; } return 0; }\n", encoding="utf-8"
        )
        (repo / "b.c").write_text(
            "int foo(int x) { if (x) { return 1; } return 0; }\n", encoding="utf-8"
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "foo"})

        assert result["success"] is False
        assert result["error"] == "ambiguous"
        assert len(result["candidates"]) == 2

    def test_qualified_name_strips_to_simple(self, tmp_path):
        """Passing ``proj.alarm.foo`` resolves to ``foo`` by last-dot suffix."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "alarm.c").write_text(
            "int foo(int x) { if (x > 0) { return 1; } return 0; }\n",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "proj.alarm.foo"})

        assert result["success"] is True
        assert len(result["predicates"]) == 1
        assert result["predicates"][0]["kind"] == "if"
        assert result["predicates"][0]["expression"] == "x > 0"
