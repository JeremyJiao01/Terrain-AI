"""Tests for api-find / find_api aggregation logic.

Covers:
- API doc generator: _sanitise_filename, _render_func_detail
- cmd_api_find CLI: result structure, API doc attachment
- _handle_find_api MCP: result structure, API doc attachment
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Unit tests for api_doc_generator helpers
# ---------------------------------------------------------------------------


class TestSanitiseFilename:
    def test_forward_slash(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("project/api/init") == "project_api_init"

    def test_backslash(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("project\\api\\init") == "project_api_init"

    def test_no_separators(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("simple_name") == "simple_name"

    def test_mixed_separators(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("a/b\\c") == "a_b_c"


class TestRenderFuncDetail:
    def test_basic_rendering(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _render_func_detail

        func = {
            "qn": "project.api.init",
            "name": "init",
            "signature": "int init(void)",
            "return_type": "int",
            "visibility": "public",
            "path": "src/api.c",
            "start_line": 10,
            "end_line": 20,
            "module_qn": "project.api",
            "docstring": "Initialize the API subsystem.",
        }

        content = _render_func_detail(func, callers=[], callees=[])

        assert "# init" in content
        assert "`int init(void)`" in content
        assert "`int`" in content
        assert "public" in content
        assert "Initialize the API subsystem." in content
        assert "*(无调用者)*" in content

    def test_with_callers_and_callees(self):
        from terrain.domains.upper.apidoc.api_doc_generator import _render_func_detail

        func = {
            "qn": "mod.foo",
            "name": "foo",
            "signature": "void foo()",
            "return_type": None,
            "visibility": "static",
            "path": "src/mod.c",
            "start_line": 5,
            "end_line": 15,
            "module_qn": "mod",
            "docstring": None,
        }

        callers = [{"qn": "mod.bar", "path": "src/mod.c", "start_line": 30}]
        callees = [{"qn": "mod.baz", "path": "src/mod.c", "start_line": 50}]

        content = _render_func_detail(func, callers=callers, callees=callees)

        assert "被调用 (1)" in content
        assert "mod.bar" in content
        # No docstring section when docstring is None
        assert "## 描述" not in content


# ---------------------------------------------------------------------------
# Unit tests for generate_api_docs pipeline
# ---------------------------------------------------------------------------


class TestGenerateApiDocs:
    def test_generates_files(self, tmp_path: Path):
        from terrain.domains.upper.apidoc.api_doc_generator import generate_api_docs

        func_rows = [
            {
                "result": [
                    "mymod",        # module_qn
                    "src/mymod.c",  # module_path
                    "mymod.do_stuff",  # qn
                    "do_stuff",     # name
                    "int do_stuff(int x)",  # signature
                    "int",          # return_type
                    "public",       # visibility
                    "x: int",       # parameters
                    "Does stuff.",  # docstring
                    1,              # start_line
                    10,             # end_line
                    "src/mymod.c",  # path
                ]
            }
        ]

        result = generate_api_docs(
            func_rows=func_rows,
            type_rows=[],
            call_rows=[],
            output_dir=tmp_path,
        )

        assert result["module_count"] == 1
        assert result["func_count"] == 1
        assert result["type_count"] == 0

        # Check generated files
        assert (tmp_path / "api_docs" / "index.md").exists()
        assert (tmp_path / "api_docs" / "modules" / "mymod.md").exists()
        assert (tmp_path / "api_docs" / "funcs" / "mymod.do_stuff.md").exists()

        # Check L3 content
        func_doc = (tmp_path / "api_docs" / "funcs" / "mymod.do_stuff.md").read_text()
        assert "# do_stuff" in func_doc
        assert "Does stuff." in func_doc

    def test_call_graph_wiring(self, tmp_path: Path):
        from terrain.domains.upper.apidoc.api_doc_generator import generate_api_docs

        func_rows = [
            {
                "result": [
                    "m", "m.c", "m.caller", "caller", "void caller()", None,
                    "public", "", None, 1, 5, "m.c",
                ]
            },
            {
                "result": [
                    "m", "m.c", "m.callee", "callee", "void callee()", None,
                    "static", "", None, 10, 15, "m.c",
                ]
            },
        ]

        call_rows = [
            {"result": ["m.caller", "m.callee", "m.c", 3]},
        ]

        generate_api_docs(
            func_rows=func_rows,
            type_rows=[],
            call_rows=call_rows,
            output_dir=tmp_path,
        )

        caller_doc = (tmp_path / "api_docs" / "funcs" / "m.caller.md").read_text()
        assert "callee" in caller_doc

        callee_doc = (tmp_path / "api_docs" / "funcs" / "m.callee.md").read_text()
        assert "caller" in callee_doc
        assert "被调用 (1)" in callee_doc


# ---------------------------------------------------------------------------
# Integration-style tests for the find_api aggregation logic
# ---------------------------------------------------------------------------


class TestFindApiAggregation:
    """Test the core aggregation logic shared by cmd_api_find and _handle_find_api.

    These tests exercise the filename-matching and doc-attachment logic
    without requiring a live database or embeddings model.
    """

    def test_doc_attachment_when_file_exists(self, tmp_path: Path):
        """When a matching API doc file exists, its content is attached."""
        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)

        doc_content = "# mymod.do_stuff\n\n- **Signature**: `int do_stuff(int x)`\n"
        (funcs_dir / "mymod.do_stuff.md").write_text(doc_content)

        # Simulate the attachment logic from cmd_api_find / _handle_find_api
        qn = "mymod.do_stuff"
        safe_qn = qn.replace("/", "_").replace("\\", "_")
        doc_file = funcs_dir / f"{safe_qn}.md"

        assert doc_file.exists()
        assert doc_file.read_text() == doc_content

    def test_doc_attachment_when_file_missing(self, tmp_path: Path):
        """When no API doc exists for a result, api_doc should be None."""
        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)

        qn = "nonexistent.function"
        safe_qn = qn.replace("/", "_").replace("\\", "_")
        doc_file = funcs_dir / f"{safe_qn}.md"

        assert not doc_file.exists()

    def test_sanitise_slash_in_qn(self, tmp_path: Path):
        """Qualified names with slashes are sanitised to underscores for lookup."""
        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)

        (funcs_dir / "path_to_func.md").write_text("doc")

        qn = "path/to/func"
        safe_qn = qn.replace("/", "_").replace("\\", "_")
        doc_file = funcs_dir / f"{safe_qn}.md"
        assert doc_file.exists()


# ---------------------------------------------------------------------------
# Unit tests for _extract_referenced_globals — comment stripping
# ---------------------------------------------------------------------------


class TestExtractReferencedGlobalsCommentStripping:
    """Verify that UPPERCASE symbols in comments are NOT included in results."""

    def _extract(self, source: str, file_ext: str = ".py") -> list[str]:
        from terrain.domains.upper.apidoc.api_doc_generator import (
            _extract_referenced_globals,
        )
        return _extract_referenced_globals(source, params=None, file_ext=file_ext)

    def test_python_hash_comment_not_included(self):
        """Symbols only in # comments should not appear in Python results."""
        src = (
            "def connect(host):\n"
            "    # Retry up to MAX_RETRIES times if connection fails\n"
            "    for i in range(3):\n"
            "        pass\n"
        )
        result = self._extract(src, ".py")
        assert "MAX_RETRIES" not in result

    def test_python_docstring_double_quote_not_included(self):
        """Symbols only in triple-double-quote docstrings should not appear."""
        src = (
            'def fetch():\n'
            '    """Fetches data.\n'
            '\n'
            '    Uses TIMEOUT_SECS as the default timeout.\n'
            '    """\n'
            '    return requests.get(url)\n'
        )
        result = self._extract(src, ".py")
        assert "TIMEOUT_SECS" not in result

    def test_python_docstring_single_quote_not_included(self):
        """Symbols only in triple-single-quote docstrings should not appear."""
        src = (
            "def fetch():\n"
            "    '''Uses BUFFER_SIZE internally.'''\n"
            "    return data\n"
        )
        result = self._extract(src, ".py")
        assert "BUFFER_SIZE" not in result

    def test_python_real_usage_still_included(self):
        """Symbols used in actual code (not comments) should still appear."""
        src = (
            "def connect(host):\n"
            "    # Retry logic\n"
            "    for i in range(MAX_RETRIES):\n"
            "        pass\n"
        )
        result = self._extract(src, ".py")
        assert "MAX_RETRIES" in result

    def test_js_line_comment_not_included(self):
        """Symbols only in // comments should not appear in JS results."""
        src = (
            "function connect(host) {\n"
            "    // Retry up to MAX_RETRIES times\n"
            "    for (let i = 0; i < 3; i++) {}\n"
            "}\n"
        )
        result = self._extract(src, ".js")
        assert "MAX_RETRIES" not in result

    def test_js_block_comment_not_included(self):
        """Symbols only in /* */ block comments should not appear in JS results."""
        src = (
            "function fetch() {\n"
            "    /* Uses CACHE_SIZE for buffering */\n"
            "    return data;\n"
            "}\n"
        )
        result = self._extract(src, ".js")
        assert "CACHE_SIZE" not in result

    def test_ts_comment_not_included(self):
        """Symbols only in comments should not appear in TS results."""
        src = (
            "function send(): void {\n"
            "    // MAX_PAYLOAD_SIZE is the upper limit\n"
            "    sendData();\n"
            "}\n"
        )
        result = self._extract(src, ".ts")
        assert "MAX_PAYLOAD_SIZE" not in result

    def test_go_line_comment_not_included(self):
        """Symbols only in // comments should not appear in Go results."""
        src = (
            "func connect(host string) {\n"
            "    // Uses MAX_CONN_POOL for pooling\n"
            "    for i := 0; i < 3; i++ {}\n"
            "}\n"
        )
        result = self._extract(src, ".go")
        assert "MAX_CONN_POOL" not in result

    def test_go_block_comment_not_included(self):
        """Symbols only in /* */ comments should not appear in Go results."""
        src = (
            "func fetch() {\n"
            "    /* RETRY_LIMIT is set at startup */\n"
            "    return\n"
            "}\n"
        )
        result = self._extract(src, ".go")
        assert "RETRY_LIMIT" not in result


# ---------------------------------------------------------------------------
# Unit tests for build_symbol_index
# ---------------------------------------------------------------------------


class TestBuildSymbolIndex:
    """Tests for the reverse symbol index builder in api_doc_generator."""

    def _write_func_doc(self, funcs_dir: Path, qn: str, symbols: list[str]) -> None:
        """Write a minimal function .md file with a 全局变量引用 section."""
        lines = [f"# {qn.split('.')[-1]}", "", f"- 模块: {'.'.join(qn.split('.')[:-1])}", ""]
        if symbols:
            lines += ["## 全局变量引用", ""]
            for sym in symbols:
                lines.append(f"- `{sym}`")
            lines.append("")
        lines += ["## 实现", "", "```c", "void stub() {}", "```", ""]
        (funcs_dir / f"{qn}.md").write_text("\n".join(lines), encoding="utf-8")

    def test_index_written(self, tmp_path: Path):
        """build_symbol_index creates symbol_index.json."""
        from terrain.domains.upper.apidoc.api_doc_generator import build_symbol_index

        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)
        api_dir = tmp_path / "api_docs"

        self._write_func_doc(funcs_dir, "mod.foo", ["MAX_SIZE", "GLOBAL_FLAG"])
        self._write_func_doc(funcs_dir, "mod.bar", ["MAX_SIZE"])

        build_symbol_index(funcs_dir, api_dir)

        assert (api_dir / "symbol_index.json").exists()

    def test_index_reverse_mapping(self, tmp_path: Path):
        """symbol_index.json maps each symbol to all functions that reference it."""
        import json

        from terrain.domains.upper.apidoc.api_doc_generator import build_symbol_index

        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)
        api_dir = tmp_path / "api_docs"

        self._write_func_doc(funcs_dir, "mod.foo", ["MAX_SIZE", "GLOBAL_FLAG"])
        self._write_func_doc(funcs_dir, "mod.bar", ["MAX_SIZE"])
        self._write_func_doc(funcs_dir, "mod.baz", [])  # no globals

        build_symbol_index(funcs_dir, api_dir)
        data = json.loads((api_dir / "symbol_index.json").read_text())

        assert sorted(data["MAX_SIZE"]) == ["mod.bar", "mod.foo"]
        assert data["GLOBAL_FLAG"] == ["mod.foo"]
        assert "mod.baz" not in str(data.get("GLOBAL_FLAG", ""))

    def test_index_meta(self, tmp_path: Path):
        """_meta counts total, with-globals, and without-globals functions."""
        import json

        from terrain.domains.upper.apidoc.api_doc_generator import build_symbol_index

        funcs_dir = tmp_path / "api_docs" / "funcs"
        funcs_dir.mkdir(parents=True)
        api_dir = tmp_path / "api_docs"

        self._write_func_doc(funcs_dir, "mod.foo", ["FLAG_A"])
        self._write_func_doc(funcs_dir, "mod.bar", [])  # no globals

        build_symbol_index(funcs_dir, api_dir)
        meta = json.loads((api_dir / "symbol_index.json").read_text())["_meta"]

        assert meta["total_funcs"] == 2
        assert meta["funcs_with_globals"] == 1
        assert meta["funcs_without_globals"] == 1

    def test_generate_api_docs_creates_symbol_index(self, tmp_path: Path):
        """generate_api_docs writes symbol_index.json alongside other docs."""
        from terrain.domains.upper.apidoc.api_doc_generator import generate_api_docs

        func_rows = [
            {
                "result": [
                    "mymod", "src/mymod.c", "mymod.do_stuff", "do_stuff",
                    "int do_stuff(int x)", "int", "public", "x: int",
                    "Does stuff.", 1, 10, "src/mymod.c",
                ]
            }
        ]

        generate_api_docs(
            func_rows=func_rows,
            type_rows=[],
            call_rows=[],
            output_dir=tmp_path,
        )

        assert (tmp_path / "api_docs" / "symbol_index.json").exists()


# ---------------------------------------------------------------------------
# Unit tests for _handle_find_symbol_in_docs with symbol index
# ---------------------------------------------------------------------------


class TestFindSymbolIndexLookup:
    """Tests for the fast-path (index-based) lookup in _handle_find_symbol_in_docs."""

    def _make_api_docs(self, tmp_path: Path, symbol_map: dict[str, list[str]]) -> Path:
        """Create a minimal api_docs directory with a symbol_index.json."""
        import json

        api_dir = tmp_path / "api_docs"
        funcs_dir = api_dir / "funcs"
        funcs_dir.mkdir(parents=True)

        # Write index
        index: dict = {}
        for sym, qns in symbol_map.items():
            index[sym] = qns
        total = sum(len(v) for v in symbol_map.values())
        index["_meta"] = {
            "total_funcs": total,
            "funcs_with_globals": total,
            "funcs_without_globals": 0,
        }
        (api_dir / "symbol_index.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

        # Write minimal .md for each referenced qn
        all_qns: set[str] = set()
        for qns in symbol_map.values():
            all_qns.update(qns)
        for qn in all_qns:
            name = qn.split(".")[-1]
            mod = ".".join(qn.split(".")[:-1])
            content = (
                f"# {name}\n\n"
                f"- 位置: src/{mod}.c:1-10\n"
                f"- 模块: {mod}\n\n"
                "## 全局变量引用\n\n"
                f"- `{list(symbol_map.keys())[0]}`\n\n"
                "## 实现\n\n```c\nvoid stub(){}\n```\n"
            )
            (funcs_dir / f"{qn}.md").write_text(content, encoding="utf-8")

        return tmp_path

    async def _call_handler(
        self, artifact_dir: Path, symbol: str, max_results: int = 30
    ) -> dict:
        """Directly invoke _handle_find_symbol_in_docs on a mock server."""
        from unittest.mock import MagicMock

        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        server = object.__new__(MCPToolsRegistry)
        server._active_artifact_dir = artifact_dir  # type: ignore[attr-defined]
        server._require_active = MagicMock()  # type: ignore[attr-defined]

        return await server._handle_find_symbol_in_docs(symbol, max_results)

    def test_uses_index_when_present(self, tmp_path: Path):
        """When symbol_index.json exists, the handler returns matches via O(1) lookup."""
        import asyncio

        artifact_dir = self._make_api_docs(tmp_path, {"MY_GLOBAL": ["mod.func_a", "mod.func_b"]})
        result = asyncio.get_event_loop().run_until_complete(
            self._call_handler(artifact_dir, "MY_GLOBAL")
        )

        assert result["match_count"] == 2
        qns = {r["qualified_name"] for r in result["results"]}
        assert qns == {"mod.func_a", "mod.func_b"}

    def test_no_match_returns_message(self, tmp_path: Path):
        """A symbol not in the index returns match_count=0 with a message."""
        import asyncio

        artifact_dir = self._make_api_docs(tmp_path, {"OTHER_SYM": ["mod.func_a"]})
        result = asyncio.get_event_loop().run_until_complete(
            self._call_handler(artifact_dir, "MISSING_SYM")
        )

        assert result["match_count"] == 0
        assert "message" in result

    def test_warning_when_funcs_without_globals(self, tmp_path: Path):
        """A 'warning' key appears when _meta.funcs_without_globals > 0."""
        import asyncio
        import json

        artifact_dir = self._make_api_docs(tmp_path, {"FLAG": ["mod.func_a"]})
        # Patch _meta to indicate some funcs lack globals
        index_path = artifact_dir / "api_docs" / "symbol_index.json"
        data = json.loads(index_path.read_text())
        data["_meta"]["funcs_without_globals"] = 5
        index_path.write_text(json.dumps(data), encoding="utf-8")

        result = asyncio.get_event_loop().run_until_complete(
            self._call_handler(artifact_dir, "FLAG")
        )

        assert "warning" in result

    def test_no_warning_when_all_funcs_indexed(self, tmp_path: Path):
        """No 'warning' key when funcs_without_globals == 0."""
        import asyncio

        artifact_dir = self._make_api_docs(tmp_path, {"FLAG": ["mod.func_a"]})
        result = asyncio.get_event_loop().run_until_complete(
            self._call_handler(artifact_dir, "FLAG")
        )

        assert "warning" not in result
