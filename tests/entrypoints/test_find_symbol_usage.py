"""Tests for the `find_symbol_usage` MCP tool (slices 1+2).

Slice 1 delivered symbol resolution and mode="read" / "all" read collection.
Slice 2 layers on write collection (direct assignments, compound assignments,
and ++/-- update expressions), merges read+write for mode="all", and adds
``qualified_scope`` filtering with a "scope not found" error for invalid
scopes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_registry(tmp_path: Path, repo: Path):
    """Build an MCPToolsRegistry pointed at *repo* without running the indexer.

    Slice 1 of find_symbol_usage computes everything on demand by parsing
    source files with tree-sitter, so we don't need a real indexed artifact.
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
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tool_appears_in_tools_list(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        names = [t.name for t in reg.tools()]
        assert "find_symbol_usage" in names

    def test_handler_dispatches(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        assert reg.get_handler("find_symbol_usage") is not None


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------


class TestSymbolResolution:
    def test_global_variable_found_in_three_functions(self, tmp_path):
        """A file-scope global referenced by 3 functions → 3 read usages."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "alarm.c").write_text(
            """\
int g_alarm = 0;

int read_one(void) {
    return g_alarm;
}

int read_two(int x) {
    if (g_alarm > x) {
        return 1;
    }
    return 0;
}

void print_alarm(void) {
    printf("%d", g_alarm);
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "read"})

        assert result["success"] is True
        assert result["matched"]["kind"] == "global"
        assert result["matched"]["qualified_name"].endswith(".g_alarm")
        assert len(result["usages"]) == 3
        for usage in result["usages"]:
            assert usage["mode"] == "read"
            assert usage["location"].startswith("alarm.c:")
            assert usage["enclosing_function"].endswith(("read_one", "read_two", "print_alarm"))
            assert usage["context"]  # non-empty source line
        functions = {u["enclosing_function"].rsplit(".", 1)[-1] for u in result["usages"]}
        assert functions == {"read_one", "read_two", "print_alarm"}

    def test_symbol_not_found(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text("int g_real = 1;\n", encoding="utf-8")

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_missing"})

        assert result["success"] is False
        assert result["error"] == "symbol not found"
        assert result["symbol"] == "g_missing"

    def test_ambiguous_static_locals(self, tmp_path):
        """Two static-local variables with the same simple name → ambiguous."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int foo(void) {
    static int counter = 0;
    counter++;
    return counter;
}
""",
            encoding="utf-8",
        )
        (repo / "b.c").write_text(
            """\
int bar(void) {
    static int counter = 42;
    return counter;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "counter"})

        assert result["success"] is False
        assert result["error"] == "ambiguous"
        assert len(result["candidates"]) == 2
        # Both candidates should be qualified names ending with "counter"
        for cand in result["candidates"]:
            assert cand.endswith(".counter")
        # Candidates should come from different scopes (different QN prefixes)
        assert len(set(result["candidates"])) == 2

    def test_qualified_name_disambiguates(self, tmp_path):
        """Passing a full qualified_name resolves to the specific static local."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int foo(void) {
    static int counter = 0;
    counter++;
    return counter;
}
""",
            encoding="utf-8",
        )
        (repo / "b.c").write_text(
            """\
int bar(void) {
    static int counter = 42;
    return counter;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        # First call returns the ambiguous candidates; pick one.
        ambiguous = _call(registry, "find_symbol_usage", {"symbol": "counter"})
        assert ambiguous["error"] == "ambiguous"
        target_qn = next(c for c in ambiguous["candidates"] if "a.foo" in c or c.endswith("a.foo.counter"))

        result = _call(registry, "find_symbol_usage", {"symbol": target_qn, "mode": "read"})
        assert result["success"] is True
        assert result["matched"]["qualified_name"] == target_qn
        assert result["matched"]["kind"] == "static_local"
        # Usages inside foo() only — 2 reads (counter++ compound-read + return counter)
        for usage in result["usages"]:
            assert usage["enclosing_function"].endswith(".foo")

    def test_enum_value_rejected(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "errors.c").write_text(
            """\
enum {
    ERR_DCI_POS = 1,
    ERR_DCI_NEG = 2,
};

int handle(int code) {
    if (code == ERR_DCI_POS) {
        return 1;
    }
    return 0;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "ERR_DCI_POS"})

        assert result["success"] is False
        assert "not a variable" in result["error"]
        assert "kind=enum" in result["error"]


# ---------------------------------------------------------------------------
# Mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_invalid_mode_raises(self, tmp_path):
        """mode must be one of read/write/all — anything else is rejected."""
        from terrain.entrypoints.mcp.tools import ToolError

        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text("int g = 0;\n", encoding="utf-8")

        registry = _make_registry(tmp_path, repo)
        handler = registry.get_handler("find_symbol_usage")
        with pytest.raises(ToolError):
            _run(handler(symbol="g", mode="bogus"))


# ---------------------------------------------------------------------------
# Write-mode collection (slice 2)
# ---------------------------------------------------------------------------


class TestWriteModeCollection:
    def test_direct_assignment(self, tmp_path):
        """`g_alarm = 0;` → assign_type=direct, op="=", rhs="0"."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm = 0;

void clear(void) { g_alarm = 0; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        # The file-scope initializer `int g_alarm = 0;` is a declaration, not an
        # assignment_expression, so only the write inside clear() is reported.
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["mode"] == "write"
        assert u["assign_type"] == "direct"
        assert u["op"] == "="
        assert u["rhs"] == "0"
        assert u["enclosing_function"].endswith(".clear")

    def test_compound_assignment_preserves_operator(self, tmp_path):
        """`g_alarm |= ERR_MASK;` → compound, op="|=", rhs="ERR_MASK" (not normalized)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm = 0;

void set_err(void) { g_alarm |= ERR_MASK; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["assign_type"] == "compound"
        assert u["op"] == "|="
        # rhs preserves the original expression verbatim — no `x | ERR_MASK` rewrite.
        assert u["rhs"] == "ERR_MASK"

    def test_all_compound_operators_covered(self, tmp_path):
        """+=  -=  *=  /=  %=  &=  |=  ^=  <<=  >>=  are all assign_type=compound."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_x = 0;

void churn(int v) {
    g_x += v;
    g_x -= v;
    g_x *= v;
    g_x /= v;
    g_x %= v;
    g_x &= v;
    g_x |= v;
    g_x ^= v;
    g_x <<= v;
    g_x >>= v;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_x", "mode": "write"})

        assert result["success"] is True
        ops = [u["op"] for u in result["usages"]]
        assert sorted(ops) == sorted(
            ["+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="]
        )
        for u in result["usages"]:
            assert u["assign_type"] == "compound"
            assert u["rhs"] == "v"

    def test_update_expression_is_compound_write(self, tmp_path):
        """`g_alarm++` / `--g_alarm` → compound, op="++"/"--" with empty rhs."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm = 0;

void tick(void) {
    g_alarm++;
    --g_alarm;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        ops = sorted(u["op"] for u in result["usages"])
        assert ops == ["++", "--"]
        for u in result["usages"]:
            assert u["assign_type"] == "compound"
            assert u["rhs"] == ""

    def test_struct_field_assignment_hits_object_symbol(self, tmp_path):
        """`g_alarm.dci = 1;` with symbol=`g_alarm` counts as a write of the object."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
struct Alarm { int dci; };
struct Alarm g_alarm;

void raise(void) { g_alarm.dci = 1; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["assign_type"] == "direct"
        assert u["op"] == "="
        assert u["rhs"] == "1"

    def test_struct_field_write_is_not_also_a_read(self, tmp_path):
        """With mode=all, `g_alarm.dci = 1;` produces one write, zero reads."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
struct Alarm { int dci; };
struct Alarm g_alarm;

void raise(void) { g_alarm.dci = 1; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "all"})

        modes = [u["mode"] for u in result["usages"]]
        assert modes == ["write"]

    def test_pointer_deref_write_is_skipped_for_mvp(self, tmp_path):
        """`(*p) = 1;` is NOT recognized as a write of `p` in the MVP."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int *p = 0;

void do_it(void) { (*p) = 1; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "p", "mode": "write"})

        assert result["success"] is True
        assert result["usages"] == []

    def test_assignment_inside_if_condition(self, tmp_path):
        """`if ((g_alarm = get()) != 0)` is a write site."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm = 0;
int get(void) { return 1; }

void probe(void) {
    if ((g_alarm = get()) != 0) {
        return;
    }
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["op"] == "="
        assert result["usages"][0]["rhs"] == "get()"


# ---------------------------------------------------------------------------
# mode="all" merges read + write and sorts by location
# ---------------------------------------------------------------------------


class TestModeAll:
    def test_reads_and_writes_sorted_by_location(self, tmp_path):
        """mode="all" merges both lists and sorts by (file, line)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm = 0;

int reader(void) { return g_alarm; }
void writer(int v) { g_alarm = v; }
void tick(void) { g_alarm++; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_alarm", "mode": "all"})

        assert result["success"] is True
        usages = result["usages"]
        assert {u["mode"] for u in usages} == {"read", "write"}

        def _line(u):
            return int(u["location"].rsplit(":", 1)[1])

        lines = [_line(u) for u in usages]
        assert lines == sorted(lines)


# ---------------------------------------------------------------------------
# qualified_scope filtering (slice 2)
# ---------------------------------------------------------------------------


class TestQualifiedScope:
    def _fixture(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "alarm_cfg.c").write_text(
            """\
int g_alarm = 0;

void AlarmCheck_DCI(void) {
    g_alarm = 0;
    g_alarm |= 0x01;
    g_alarm++;
}

void OtherCheck(void) {
    g_alarm = 1;
}
""",
            encoding="utf-8",
        )
        return repo

    def test_function_scope_filters_other_functions(self, tmp_path):
        repo = self._fixture(tmp_path)
        registry = _make_registry(tmp_path, repo)
        result = _call(
            registry,
            "find_symbol_usage",
            {
                "symbol": "g_alarm",
                "mode": "write",
                "qualified_scope": "proj.alarm_cfg.AlarmCheck_DCI",
            },
        )

        assert result["success"] is True
        # Only the three writes inside AlarmCheck_DCI.
        assert len(result["usages"]) == 3
        for u in result["usages"]:
            assert u["enclosing_function"] == "proj.alarm_cfg.AlarmCheck_DCI"

    def test_module_scope_includes_all_functions_in_module(self, tmp_path):
        repo = self._fixture(tmp_path)
        registry = _make_registry(tmp_path, repo)
        result = _call(
            registry,
            "find_symbol_usage",
            {"symbol": "g_alarm", "mode": "write", "qualified_scope": "proj.alarm_cfg"},
        )

        assert result["success"] is True
        # Three writes in AlarmCheck_DCI + one in OtherCheck = 4.
        assert len(result["usages"]) == 4

    def test_scope_not_found_returns_error(self, tmp_path):
        repo = self._fixture(tmp_path)
        registry = _make_registry(tmp_path, repo)
        result = _call(
            registry,
            "find_symbol_usage",
            {"symbol": "g_alarm", "qualified_scope": "proj.nonexistent"},
        )

        assert result["success"] is False
        assert result["error"] == "scope not found: proj.nonexistent"

    def test_scope_filter_applies_to_mode_all(self, tmp_path):
        repo = self._fixture(tmp_path)
        registry = _make_registry(tmp_path, repo)
        # Add a reader in AlarmCheck_DCI by augmenting the fixture.
        (repo / "alarm_cfg.c").write_text(
            """\
int g_alarm = 0;

void AlarmCheck_DCI(void) {
    if (g_alarm) {
        g_alarm = 0;
    }
}

void OtherCheck(void) {
    g_alarm = 1;
}
""",
            encoding="utf-8",
        )
        result = _call(
            registry,
            "find_symbol_usage",
            {
                "symbol": "g_alarm",
                "mode": "all",
                "qualified_scope": "proj.alarm_cfg.AlarmCheck_DCI",
            },
        )

        assert result["success"] is True
        for u in result["usages"]:
            assert u["enclosing_function"] == "proj.alarm_cfg.AlarmCheck_DCI"
        modes = {u["mode"] for u in result["usages"]}
        assert modes == {"read", "write"}


# ---------------------------------------------------------------------------
# Read-mode usage filtering
# ---------------------------------------------------------------------------


class TestReadUsageFiltering:
    def test_assignment_lhs_is_not_a_read(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_val = 0;

void set_it(int v) { g_val = v; }
int get_it(void) { return g_val; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_val", "mode": "read"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["enclosing_function"].endswith(".get_it")

    def test_field_access_with_same_name_is_excluded(self, tmp_path):
        """`obj.g_val` is NOT a use of the global `g_val`."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
struct S { int g_val; };
int g_val = 0;

int read_global(void) { return g_val; }
int read_field(struct S *s) { return s->g_val; }
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_usage", {"symbol": "g_val", "mode": "read"})

        assert result["success"] is True
        # Only read_global counts — the struct field access does not.
        funcs = {u["enclosing_function"].rsplit(".", 1)[-1] for u in result["usages"]}
        assert funcs == {"read_global"}
