"""Tests for AST-level variable usage via the `find_symbol_in_docs` MCP tool.

Previously delivered by the standalone `find_symbol_usage` tool and merged
here after JER-79: when a caller supplies ``mode`` or ``qualified_scope``
`find_symbol_in_docs` switches from the doc-based UPPER_CASE reference
search to the AST-level read/write scanner covered by these tests (symbol
resolution, direct/compound/via_memcpy/address_of/pointer_deref_write
writes, qualified_scope filtering, static-local isolation, and assorted
edge cases).
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

    AST-level variable usage is computed on demand by parsing source files
    with tree-sitter, so we don't need a real indexed artifact.
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
    kwargs = dict(args or {})
    # Every test in this file targets AST-level variable usage — make sure the
    # call opts into that mode by defaulting `mode="all"` when the test omits
    # both `mode` and `qualified_scope`.
    if (
        tool_name == "find_symbol_in_docs"
        and "mode" not in kwargs
        and "qualified_scope" not in kwargs
    ):
        kwargs["mode"] = "all"
    result = _run(handler(**kwargs))
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tool_appears_in_tools_list(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        names = [t.name for t in reg.tools()]
        assert "find_symbol_in_docs" in names
        # find_symbol_usage was merged into find_symbol_in_docs (JER-79) and
        # should no longer be advertised as a separate tool.
        assert "find_symbol_usage" not in names

    def test_handler_dispatches(self, tmp_path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        assert reg.get_handler("find_symbol_in_docs") is not None
        assert reg.get_handler("find_symbol_usage") is None

    def test_tool_schema_advertises_mode_and_qualified_scope(self, tmp_path):
        """AST-level mode is opt-in via `mode` / `qualified_scope` parameters."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        reg = MCPToolsRegistry(tmp_path / "workspace")
        tool = next(t for t in reg.tools() if t.name == "find_symbol_in_docs")
        props = tool.input_schema["properties"]
        assert "mode" in props
        assert props["mode"]["enum"] == ["read", "write", "all"]
        assert "qualified_scope" in props


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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "read"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_missing"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "counter"})

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
        ambiguous = _call(registry, "find_symbol_in_docs", {"symbol": "counter"})
        assert ambiguous["error"] == "ambiguous"
        target_qn = next(c for c in ambiguous["candidates"] if "a.foo" in c or c.endswith("a.foo.counter"))

        result = _call(registry, "find_symbol_in_docs", {"symbol": target_qn, "mode": "read"})
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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "ERR_DCI_POS"})

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
        handler = registry.get_handler("find_symbol_in_docs")
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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_x", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "all"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "p", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "all"})

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
            "find_symbol_in_docs",
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
            "find_symbol_in_docs",
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
            "find_symbol_in_docs",
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
            "find_symbol_in_docs",
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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_val", "mode": "read"})

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
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_val", "mode": "read"})

        assert result["success"] is True
        # Only read_global counts — the struct field access does not.
        funcs = {u["enclosing_function"].rsplit(".", 1)[-1] for u in result["usages"]}
        assert funcs == {"read_global"}


# ---------------------------------------------------------------------------
# via_memcpy detection (slice 3)
# ---------------------------------------------------------------------------


class TestViaMemcpy:
    def test_memcpy_with_address_first_arg(self, tmp_path):
        """`memcpy(&g_alarm, src, n)` → assign_type=via_memcpy, op=memcpy."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm;

void copy_in(const void *src, unsigned n) {
    memcpy(&g_alarm, src, n);
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["mode"] == "write"
        assert u["assign_type"] == "via_memcpy"
        assert u["op"] == "memcpy"
        assert "memcpy(&g_alarm, src, n)" in u["rhs"]

    def test_memset_with_address_first_arg(self, tmp_path):
        """`memset(&g, 0, sizeof(g))` → via_memcpy with op=memset."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g;

void clear(void) {
    memset(&g, 0, sizeof(g));
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["assign_type"] == "via_memcpy"
        assert u["op"] == "memset"

    def test_array_first_arg_no_address(self, tmp_path):
        """`memcpy(g_buf, src, n)` (array name auto address-of) → via_memcpy."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
char g_buf[64];

void copy_buf(const char *src, unsigned n) {
    memcpy(g_buf, src, n);
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_buf", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["assign_type"] == "via_memcpy"
        assert result["usages"][0]["op"] == "memcpy"


# ---------------------------------------------------------------------------
# address_of detection (slice 3)
# ---------------------------------------------------------------------------


class TestAddressOf:
    def test_unknown_func_with_address_arg(self, tmp_path):
        """`clear(&g_alarm)` → address_of (not memcpy whitelist, not readonly)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm;
void clear(int *p);

void run(void) {
    clear(&g_alarm);
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["assign_type"] == "address_of"
        assert u["op"] == "clear"
        assert "clear(&g_alarm)" in u["rhs"]

    def test_address_in_non_first_arg(self, tmp_path):
        """`update(0, &g_alarm)` → address_of (non-first arg)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm;
void update(int flag, int *p);

void run(void) {
    update(0, &g_alarm);
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["assign_type"] == "address_of"
        assert result["usages"][0]["op"] == "update"

    def test_readonly_api_does_not_register(self, tmp_path):
        """`memcmp(&g, &other, sizeof(g))` is NOT recorded — readonly whitelist."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g;
int other;

int eq(void) {
    return memcmp(&g, &other, sizeof(g)) == 0;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})

        assert result["success"] is True
        assert result["usages"] == []

    def test_printf_with_address_does_not_register(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g;

void dump(void) {
    printf("%p", (void *)&g);
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})
        assert result["success"] is True
        assert result["usages"] == []

    def test_two_globals_both_address_of(self, tmp_path):
        """`foo(&g_a, &g_b)` with symbol=g_a is address_of (foo not memcpy)."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_a;
int g_b;
void foo(int *a, int *b);

void run(void) {
    foo(&g_a, &g_b);
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_a", "mode": "write"})
        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["assign_type"] == "address_of"
        assert result["usages"][0]["op"] == "foo"


# ---------------------------------------------------------------------------
# pointer_deref_write detection (slice 3)
# ---------------------------------------------------------------------------


class TestPointerDerefWrite:
    def test_local_alias_then_deref_write(self, tmp_path):
        """`int *p = &g; *p = 1;` → pointer_deref_write of g."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm;

void poke(void) {
    int *p = &g_alarm;
    *p = 1;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["assign_type"] == "pointer_deref_write"
        assert u["enclosing_function"].endswith(".poke")

    def test_unknown_pointer_source_is_not_aliased(self, tmp_path):
        """`int *p = get_ptr(); *p = 1;` does NOT count as a write of g."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_alarm;
int *get_ptr(void);

void poke(void) {
    int *p = get_ptr();
    *p = 1;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})

        assert result["success"] is True
        assert result["usages"] == []

    def test_alias_does_not_leak_across_functions(self, tmp_path):
        """`int *p = &g;` in foo() does not make `*p = 1;` in bar() a write of g."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g;

void foo(void) {
    int *p = &g;
    (void)p;
}

void bar(int *p) {
    *p = 1;
}
""",
            encoding="utf-8",
        )

        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})

        assert result["success"] is True
        # Only foo() has the alias, and it doesn't deref-write — so zero hits.
        assert result["usages"] == []

    def test_alias_via_assignment_after_decl(self, tmp_path):
        """Assignment-style alias: `int *p; ... p = &g; *p = 1;`."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g;

void poke(void) {
    int *p;
    p = &g;
    *p = 2;
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})
        assert result["success"] is True
        assert len(result["usages"]) == 1
        assert result["usages"][0]["assign_type"] == "pointer_deref_write"


# ---------------------------------------------------------------------------
# kind distinction: global vs static_local (slice 3 hardening)
# ---------------------------------------------------------------------------


class TestKindDistinction:
    def test_static_local_kind(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
void foo(void) {
    static int local_cnt = 0;
    local_cnt++;
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "local_cnt"})
        assert result["success"] is True
        assert result["matched"]["kind"] == "static_local"
        # Qualified name includes the function name segment.
        assert ".foo.local_cnt" in result["matched"]["qualified_name"]

    def test_file_scope_static_is_global(self, tmp_path):
        """`static int counter;` at file scope is `global`, not `static_local`."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
static int counter = 0;

void tick(void) { counter++; }
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "counter"})
        assert result["success"] is True
        assert result["matched"]["kind"] == "global"


# ---------------------------------------------------------------------------
# Edge-case fixtures: hardening (slice 3)
# ---------------------------------------------------------------------------


class TestEdgeCaseFixtures:
    def test_volatile_global_read_and_write(self, tmp_path):
        """`volatile uint32_t g_reg;` reads + writes are detected like any global."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "reg.c").write_text(
            """\
typedef unsigned int uint32_t;
volatile uint32_t g_reg;

void hw_set(uint32_t v) { g_reg = v; }
uint32_t hw_get(void) { return g_reg; }
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_reg", "mode": "all"})
        assert result["success"] is True
        modes = [u["mode"] for u in result["usages"]]
        assert sorted(modes) == ["read", "write"]

    def test_macro_expansion_assignment_not_misdetected(self, tmp_path):
        """`SET_FLAG(g)` expands to an assignment, but tree-sitter sees only
        a `call_expression`. We must NOT report a write."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
#define SET_FLAG(f) ((f) = 1)
int g;

void run(void) {
    SET_FLAG(g);
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g", "mode": "write"})
        assert result["success"] is True
        # SET_FLAG looks like a call but `g` is passed as a bare identifier (not
        # &g), and SET_FLAG is not in the memcpy whitelist — so no write.
        assert result["usages"] == []

    def test_array_subscript_write_is_direct(self, tmp_path):
        """`g_array[i] = 1;` with symbol=g_array is a write of g_array."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "a.c").write_text(
            """\
int g_array[8];

void store(int i, int v) {
    g_array[i] = v;
}
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_array", "mode": "write"})
        assert result["success"] is True
        assert len(result["usages"]) == 1
        u = result["usages"][0]
        assert u["mode"] == "write"
        assert u["assign_type"] == "direct"

    def test_combined_fixture(self, tmp_path):
        """A single C file exercising every assign_type produced by slice 3."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / "all.c").write_text(
            """\
int g_alarm;

void direct_w(int v) { g_alarm = v; }
void compound_w(int v) { g_alarm |= v; }
void update_w(void) { g_alarm++; }
void memcpy_w(const void *src, unsigned n) { memcpy(&g_alarm, src, n); }
void addrof_w(void) { clear(&g_alarm); }
void deref_w(void) {
    int *p = &g_alarm;
    *p = 1;
}
void readonly_use(void) { (void)memcmp(&g_alarm, &g_alarm, sizeof(g_alarm)); }
""",
            encoding="utf-8",
        )
        registry = _make_registry(tmp_path, repo)
        result = _call(registry, "find_symbol_in_docs", {"symbol": "g_alarm", "mode": "write"})
        assert result["success"] is True
        types = sorted(u["assign_type"] for u in result["usages"])
        assert types == [
            "address_of",
            "compound",
            "compound",  # update_expression
            "direct",
            "pointer_deref_write",
            "via_memcpy",
        ]
