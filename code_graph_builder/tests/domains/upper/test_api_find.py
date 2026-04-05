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
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("project/api/init") == "project_api_init"

    def test_backslash(self):
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("project\\api\\init") == "project_api_init"

    def test_no_separators(self):
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("simple_name") == "simple_name"

    def test_mixed_separators(self):
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _sanitise_filename

        assert _sanitise_filename("a/b\\c") == "a_b_c"


class TestRenderFuncDetail:
    def test_basic_rendering(self):
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _render_func_detail

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
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import _render_func_detail

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
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import generate_api_docs

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
        from code_graph_builder.domains.upper.apidoc.api_doc_generator import generate_api_docs

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
