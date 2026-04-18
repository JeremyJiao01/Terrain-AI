"""Tests for the ``extract_predicates`` MCP tool (slice 1-3/3 of JER-47).

Slice 1 MVP returns the predicate skeleton — ``kind``, ``location``,
``expression`` and ``nesting_path``. Slice 2 adds ``symbols_referenced`` and
``guarded_block.{start_line, end_line, contains_calls}``. Slice 3 adds
``guarded_block.contains_assignments`` and ``guarded_block.has_early_return``.
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


# ---------------------------------------------------------------------------
# Slice 2: symbols_referenced
# ---------------------------------------------------------------------------


class TestSymbolsReferenced:
    def test_if_condition_basic(self, tmp_path):
        """``if (a > b && c != NULL)`` → order-preserving ["a","b","c","NULL"]."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int a, int b, int *c) {\n"
            "    if (a > b && c != NULL) { return 1; }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        assert len(result["predicates"]) == 1
        assert result["predicates"][0]["symbols_referenced"] == ["a", "b", "c", "NULL"]

    def test_dedup_preserves_first_order(self, tmp_path):
        """Duplicate symbols keep only their first occurrence."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int a, int b) {\n"
            "    if (a > 0 && b > 0 && a < b) { return 1; }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        assert result["predicates"][0]["symbols_referenced"] == ["a", "b"]

    def test_field_access_skips_field(self, tmp_path):
        """``obj.field`` — only ``obj`` is captured, not ``field``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "struct S { int x; };\n"
            "int f(struct S obj) {\n"
            "    if (obj.x > 0) { return 1; }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        assert result["predicates"][0]["symbols_referenced"] == ["obj"]

    def test_sizeof_type_is_not_a_symbol(self, tmp_path):
        """``sizeof(struct Foo)`` — ``Foo`` is a type_identifier, excluded."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "struct Foo { int x; };\n"
            "int f(int n) {\n"
            "    if (n > sizeof(struct Foo)) { return 1; }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        assert result["predicates"][0]["symbols_referenced"] == ["n"]

    def test_function_pointer_goes_into_symbols_and_calls(self, tmp_path):
        """``cb(x)`` — ``cb`` appears in both ``symbols_referenced`` and
        ``contains_calls``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int (*cb)(int), int x) {\n"
            "    if (cb(x) > 0) {\n"
            "        return cb(x);\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        p = result["predicates"][0]
        assert "cb" in p["symbols_referenced"]
        assert "x" in p["symbols_referenced"]
        assert p["guarded_block"]["contains_calls"] == ["cb"]

    def test_for_init_cond_update_all_contribute(self, tmp_path):
        """``for (i=0; i<n; i++)`` — i and n both captured in source order."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int n) {\n"
            "    int i;\n"
            "    for (i = 0; i < n; i++) {\n"
            "        n--;\n"
            "    }\n"
            "    return n;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        for_pred = next(p for p in result["predicates"] if p["kind"] == "for")
        assert for_pred["symbols_referenced"] == ["i", "n"]

    def test_switch_case_value_symbols(self, tmp_path):
        """switch_case: symbols in the case value (constant, usually empty or a
        single macro identifier)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "#define ERR_A 1\n"
            "#define ERR_B 2\n"
            "int dispatch(int code) {\n"
            "    switch (code) {\n"
            "        case ERR_A: return 10;\n"
            "        case ERR_B: return 20;\n"
            "        default: return 0;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "dispatch"})

        assert result["success"] is True
        cases = [p for p in result["predicates"] if p["kind"] == "switch_case"]
        assert cases[0]["symbols_referenced"] == ["ERR_A"]
        assert cases[1]["symbols_referenced"] == ["ERR_B"]
        assert cases[2]["symbols_referenced"] == []  # default

    def test_string_literals_do_not_leak(self, tmp_path):
        """Identifiers inside string literals are not captured."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int strcmp(const char *a, const char *b);\n"
            "int f(const char *s) {\n"
            '    if (strcmp(s, "banana") == 0) { return 1; }\n'
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        syms = result["predicates"][0]["symbols_referenced"]
        assert "banana" not in syms
        # strcmp (the function identifier) and s should be in there
        assert "s" in syms


# ---------------------------------------------------------------------------
# Slice 2: guarded_block (start_line / end_line / contains_calls)
# ---------------------------------------------------------------------------


class TestGuardedBlock:
    def test_contains_calls_dedup_and_order(self, tmp_path):
        """``if (x) { foo(); bar(); foo(); }`` → ["foo","bar"]."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "void foo(void);\n"
            "void bar(void);\n"
            "int f(int x) {\n"
            "    if (x) {\n"
            "        foo();\n"
            "        bar();\n"
            "        foo();\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        p = result["predicates"][0]
        assert p["guarded_block"]["contains_calls"] == ["foo", "bar"]

    def test_if_guarded_block_line_numbers(self, tmp_path):
        """Block lines cover the braces (or first to last inner statement)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        # Line numbers:
        # 1 void foo(void);
        # 2 int f(int x) {
        # 3     if (x) {
        # 4         foo();
        # 5     }
        # 6     return 0;
        # 7 }
        (repo / "c.c").write_text(
            "void foo(void);\n"
            "int f(int x) {\n"
            "    if (x) {\n"
            "        foo();\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        # consequence is the compound_statement spanning `{` (line 3) to `}` (line 5)
        assert block["start_line"] == 3
        assert block["end_line"] == 5

    def test_while_body_lines_and_calls(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "void tick(void);\n"
            "int f(int n) {\n"
            "    while (n > 0) {\n"
            "        tick();\n"
            "        n--;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["start_line"] == 3
        assert block["end_line"] == 6
        assert block["contains_calls"] == ["tick"]

    def test_empty_block_has_no_calls(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int x) {\n"
            "    if (x) {\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_calls"] == []

    def test_switch_case_block_calls(self, tmp_path):
        """switch_case guarded_block = the case body (statements after `:`)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "void alpha(void);\n"
            "void beta(void);\n"
            "int dispatch(int code) {\n"
            "    switch (code) {\n"
            "        case 1:\n"
            "            alpha();\n"
            "            beta();\n"
            "            return 10;\n"
            "        default:\n"
            "            return 0;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "dispatch"})

        assert result["success"] is True
        cases = [p for p in result["predicates"] if p["kind"] == "switch_case"]
        assert cases[0]["guarded_block"]["contains_calls"] == ["alpha", "beta"]
        assert cases[1]["guarded_block"]["contains_calls"] == []

    def test_for_body(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "void step(int i);\n"
            "int f(int n) {\n"
            "    for (int i = 0; i < n; i++) {\n"
            "        step(i);\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_calls"] == ["step"]
        assert block["start_line"] == 3
        assert block["end_line"] == 5


# ---------------------------------------------------------------------------
# Slice 3: contains_assignments + has_early_return
# ---------------------------------------------------------------------------


class TestContainsAssignments:
    def test_simple_assignment_and_return(self, tmp_path):
        """``if (x) { y = 1; z = y + 2; return; }`` — two assignments +
        ``has_early_return == True``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int x) {\n"
            "    int y;\n"
            "    int z;\n"
            "    if (x) {\n"
            "        y = 1;\n"
            "        z = y + 2;\n"
            "        return 0;\n"
            "    }\n"
            "    return z;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_assignments"] == [
            {"line": 5, "lhs": "y", "rhs": "1"},
            {"line": 6, "lhs": "z", "rhs": "y + 2"},
        ]
        assert block["has_early_return"] is True

    def test_compound_assignment_keeps_op(self, tmp_path):
        """``x += 1`` → ``{"lhs":"x","rhs":"1","op":"+="}``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int x) {\n"
            "    if (x) {\n"
            "        x += 1;\n"
            "    }\n"
            "    return x;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_assignments"] == [
            {"line": 3, "lhs": "x", "rhs": "1", "op": "+="},
        ]
        assert block["has_early_return"] is False

    def test_goto_style_error_handling(self, tmp_path):
        """``if (err) goto fail;`` — ``has_early_return == True``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int err) {\n"
            "    if (err) goto fail;\n"
            "    return 0;\n"
            "fail:\n"
            "    return -1;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["has_early_return"] is True
        assert block["contains_assignments"] == []

    def test_local_variable_init_captured(self, tmp_path):
        """``int local = compute();`` — ``{"lhs":"local","rhs":"compute()"}``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int compute(void);\n"
            "int f(int x) {\n"
            "    if (x) {\n"
            "        int local = compute();\n"
            "        return local;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_assignments"] == [
            {"line": 4, "lhs": "local", "rhs": "compute()"},
        ]
        assert block["has_early_return"] is True

    def test_local_declaration_without_init_skipped(self, tmp_path):
        """``int local;`` (no initializer) — not in ``contains_assignments``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int x) {\n"
            "    if (x) {\n"
            "        int local;\n"
            "        local = 5;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_assignments"] == [
            {"line": 4, "lhs": "local", "rhs": "5"},
        ]

    def test_break_and_continue_count_as_early_return(self, tmp_path):
        """``break`` / ``continue`` in a guarded block both flip
        ``has_early_return``."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int n) {\n"
            "    while (n > 0) {\n"
            "        if (n == 5) { break; }\n"
            "        if (n == 3) { continue; }\n"
            "        n--;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        inner = [p for p in result["predicates"] if p["kind"] == "if"]
        assert inner[0]["guarded_block"]["has_early_return"] is True
        assert inner[1]["guarded_block"]["has_early_return"] is True

    def test_nested_return_propagates_outward(self, tmp_path):
        """A ``return`` nested inside an inner ``if`` also flips the outer
        block's ``has_early_return`` — the path exists."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int a, int b) {\n"
            "    if (a) {\n"
            "        if (b) {\n"
            "            return 1;\n"
            "        }\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        preds = sorted(result["predicates"], key=lambda p: p["location"])
        # Outer `if (a)` should have has_early_return == True even though the
        # return is two levels deeper.
        assert preds[0]["guarded_block"]["has_early_return"] is True
        assert preds[1]["guarded_block"]["has_early_return"] is True

    def test_empty_body_has_no_assignments_or_early_return(self, tmp_path):
        """``if (x);`` — empty body: both fields are empty / False."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "int f(int x) {\n"
            "    if (x);\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert block["contains_assignments"] == []
        assert block["has_early_return"] is False

    def test_assignment_inside_call_argument(self, tmp_path):
        """``foo(x = y)`` — the ``x = y`` assignment_expression is collected."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "c.c").write_text(
            "void foo(int v);\n"
            "int f(int cond, int y) {\n"
            "    int x;\n"
            "    if (cond) {\n"
            "        foo(x = y);\n"
            "    }\n"
            "    return x;\n"
            "}\n",
            encoding="utf-8",
        )
        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "f"})

        assert result["success"] is True
        block = result["predicates"][0]["guarded_block"]
        assert {"line": 5, "lhs": "x", "rhs": "y"} in block["contains_assignments"]

    def test_industrial_style_deep_nesting_end_to_end(self, tmp_path):
        """≥ 5-level nested realistic alarm-handler style C — verify every
        predicate has the slice-3 fields populated as expected."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "alarm_cfg.c").write_text(
            """\
int SetAlarm(int dci);
int LogDebug(const char *msg);

int AlarmCheck_DCI(int grid_ok, double dci_filtered, int dci_cnt,
                   int system_ready, int user_ack) {
    int fault_cnt = 0;
    if (grid_ok) {
        if (system_ready) {
            if (dci_filtered > 0.5) {
                if (dci_cnt > 3) {
                    if (!user_ack) {
                        fault_cnt += 1;
                        SetAlarm(1);
                        return -1;
                    }
                }
            }
        }
    }
    return fault_cnt;
}
""",
            encoding="utf-8",
        )

        reg = _make_registry(tmp_path, repo)
        result = _call(reg, "extract_predicates", {"qualified_name": "AlarmCheck_DCI"})

        assert result["success"] is True
        preds = result["predicates"]
        assert len(preds) == 5
        # Every outer predicate sees the nested return.
        for p in preds:
            assert p["guarded_block"]["has_early_return"] is True
        # Innermost if contains the two assignments + SetAlarm call + return.
        innermost = preds[-1]
        assigns = innermost["guarded_block"]["contains_assignments"]
        assert {"line": 12, "lhs": "fault_cnt", "rhs": "1", "op": "+="} in assigns
        assert innermost["guarded_block"]["contains_calls"] == ["SetAlarm"]
        # Deepest nesting path is 4 entries (outer if's).
        assert len(innermost["nesting_path"]) == 4
