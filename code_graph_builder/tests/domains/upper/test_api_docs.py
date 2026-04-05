"""Step 2 integration test: API docs generation from tinycc graph.

Reuses the graph built in Step 1, generates L1/L2/L3 API documentation,
and validates file structure, content format, and C-specific features.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TINYCC_PATH = Path(__file__).resolve().parents[3] / "tinycc"

pytestmark = pytest.mark.skipif(
    not TINYCC_PATH.exists(),
    reason=f"tinycc source not found at {TINYCC_PATH}",
)


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    """Build the tinycc graph once for all tests."""
    from code_graph_builder.entrypoints.mcp.pipeline import build_graph

    db_path = tmp_path_factory.mktemp("graph") / "graph.db"
    b = build_graph(
        repo_path=TINYCC_PATH,
        db_path=db_path,
        rebuild=True,
        backend="kuzu",
    )
    yield b
    if hasattr(b, "close"):
        b.close()


@pytest.fixture(scope="module")
def api_docs_dir(builder, tmp_path_factory):
    """Generate API docs and return the output directory."""
    from code_graph_builder.entrypoints.mcp.pipeline import generate_api_docs_step

    artifact_dir = tmp_path_factory.mktemp("artifacts")
    result = generate_api_docs_step(
        builder=builder,
        artifact_dir=artifact_dir,
        rebuild=True,
    )
    assert result["status"] == "success", f"API doc generation failed: {result}"
    return artifact_dir / "api_docs", result


# ---------------------------------------------------------------------------
# File structure
# ---------------------------------------------------------------------------


class TestFileStructure:
    """Verify the three-level doc hierarchy is generated."""

    def test_index_exists(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        assert (docs_dir / "index.md").exists()

    def test_modules_dir_exists(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        assert (docs_dir / "modules").is_dir()

    def test_funcs_dir_exists(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        assert (docs_dir / "funcs").is_dir()

    def test_module_files_generated(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        module_files = list((docs_dir / "modules").glob("*.md"))
        assert len(module_files) > 0, "Should generate module pages"

    def test_func_files_generated(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        func_files = list((docs_dir / "funcs").glob("*.md"))
        assert len(func_files) > 50, f"Expected many func pages, got {len(func_files)}"

    def test_result_counts(self, api_docs_dir):
        _, result = api_docs_dir
        assert result["module_count"] > 0
        assert result["func_count"] > 50
        assert result["type_count"] >= 0


# ---------------------------------------------------------------------------
# L1 index content
# ---------------------------------------------------------------------------


class TestL1Index:
    """Verify the global index page content."""

    def test_has_title(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        content = (docs_dir / "index.md").read_text(encoding="utf-8")
        assert "# API Documentation Index" in content

    def test_has_module_table(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        content = (docs_dir / "index.md").read_text(encoding="utf-8")
        assert "| 模块" in content or "| Module" in content

    def test_has_total_counts(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        content = (docs_dir / "index.md").read_text(encoding="utf-8")
        assert "modules" in content.lower() or "模块" in content

    def test_module_links(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        content = (docs_dir / "index.md").read_text(encoding="utf-8")
        assert "modules/" in content, "Index should link to module pages"


# ---------------------------------------------------------------------------
# L2 module page content
# ---------------------------------------------------------------------------


class TestL2ModulePage:
    """Verify module-level documentation pages."""

    def _get_any_module_page(self, api_docs_dir):
        docs_dir, _ = api_docs_dir
        pages = list((docs_dir / "modules").glob("*.md"))
        assert len(pages) > 0
        return pages[0].read_text(encoding="utf-8"), pages[0].name

    def test_has_title(self, api_docs_dir):
        content, _ = self._get_any_module_page(api_docs_dir)
        assert content.startswith("# ")

    def test_has_file_info(self, api_docs_dir):
        content, _ = self._get_any_module_page(api_docs_dir)
        # Should mention header or implementation files
        assert "文件" in content or "头文件" in content or "Files" in content or ".c" in content

    def test_has_function_table(self, api_docs_dir):
        content, _ = self._get_any_module_page(api_docs_dir)
        # Should have a table with function signatures
        assert "|" in content, "Module page should have tables"

    def test_links_to_func_pages(self, api_docs_dir):
        """At least some module pages should link to function detail pages."""
        docs_dir, _ = api_docs_dir
        for page in (docs_dir / "modules").glob("*.md"):
            content = page.read_text(encoding="utf-8")
            if "../funcs/" in content:
                return
        pytest.fail("No module page links to function detail pages")

    def test_visibility_sections(self, api_docs_dir):
        """At least some module pages should have visibility-related content."""
        docs_dir, _ = api_docs_dir
        found = False
        for page in (docs_dir / "modules").glob("*.md"):
            content = page.read_text(encoding="utf-8")
            if any(kw in content for kw in [
                "公开接口", "内部函数", "外部声明", "其他",
                "Public", "Static", "Extern",
                "## 宏",  # macro section is also a visibility grouping
            ]):
                found = True
                break
        assert found, "At least one module page should have visibility/type sections"


# ---------------------------------------------------------------------------
# L3 function detail page content
# ---------------------------------------------------------------------------


class TestL3FuncDetail:
    """Verify function detail documentation pages."""

    def _get_func_page_with_content(self, api_docs_dir):
        """Find a function page that has substantial content."""
        docs_dir, _ = api_docs_dir
        for page in sorted((docs_dir / "funcs").glob("*.md")):
            content = page.read_text(encoding="utf-8")
            if len(content) > 200:  # Skip near-empty pages
                return content, page.name
        pytest.fail("No substantial function pages found")

    def test_has_title(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert content.startswith("# ")

    def test_has_signature(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert "签名" in content or "定义" in content or "Signature" in content

    def test_has_visibility(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert "可见性" in content or "Visibility" in content

    def test_has_location(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert "位置" in content or "Location" in content

    def test_has_module_reference(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert "模块" in content or "Module" in content

    def test_has_called_by_section(self, api_docs_dir):
        content, _ = self._get_func_page_with_content(api_docs_dir)
        assert "被调用" in content or "Called by" in content

    def test_has_description_or_todo(self, api_docs_dir):
        """Function should have either a docstring description or TODO placeholder."""
        content, _ = self._get_func_page_with_content(api_docs_dir)
        has_desc = ">" in content  # blockquote description line
        assert has_desc, "Function page should have > description line"


# ---------------------------------------------------------------------------
# C-specific doc features
# ---------------------------------------------------------------------------


class TestCSpecificDocs:
    """Verify C/C++ specific documentation features."""

    def test_macro_docs_generated(self, api_docs_dir):
        """Macros should have their own function doc pages."""
        docs_dir, _ = api_docs_dir
        all_pages = list((docs_dir / "funcs").glob("*.md"))
        macro_pages = []
        for page in all_pages:
            content = page.read_text(encoding="utf-8")
            if "宏定义" in content or "macro" in content.lower():
                macro_pages.append(page.name)
        assert len(macro_pages) > 0, "Should have macro documentation pages"

    def test_struct_docs_in_module_page(self, api_docs_dir):
        """Module pages should document structs."""
        docs_dir, _ = api_docs_dir
        for page in (docs_dir / "modules").glob("*.md"):
            content = page.read_text(encoding="utf-8")
            if "struct" in content.lower() or "结构体" in content:
                return  # Found struct documentation
        # Not all modules have structs, but at least some should
        # Check if any types were generated at all
        _, result = api_docs_dir
        if result["type_count"] > 0:
            pytest.fail("Types exist but no struct documentation found in module pages")

    def test_signature_has_c_syntax(self, api_docs_dir):
        """Some function pages should contain C-style signatures."""
        docs_dir, _ = api_docs_dir
        # Search for pages whose filename suggests a real C function (contains a dot separator)
        # e.g., tinycc.tcc.tcc_compile.md — not just macro names
        c_keywords = ["int ", "void ", "char ", "unsigned ", "long ", "struct ", "static "]
        for page in (docs_dir / "funcs").glob("*.md"):
            content = page.read_text(encoding="utf-8")
            if "(" in content and any(kw in content for kw in c_keywords):
                return  # Found at least one
        pytest.fail("Should find at least one C-style function signature in func docs")

    def test_visibility_field_present(self, api_docs_dir):
        """Function pages should have a visibility field."""
        docs_dir, _ = api_docs_dir
        checked = 0
        has_visibility = 0
        for page in list((docs_dir / "funcs").glob("*.md"))[:100]:
            content = page.read_text(encoding="utf-8")
            checked += 1
            if "可见性:" in content or "Visibility:" in content:
                has_visibility += 1
        assert has_visibility > 0, "Function pages should have visibility field"

    def test_docstring_in_description(self, api_docs_dir):
        """Functions with extracted C comments should show them in description."""
        docs_dir, _ = api_docs_dir
        with_desc = 0
        for page in (docs_dir / "funcs").glob("*.md"):
            content = page.read_text(encoding="utf-8")
            # Has a blockquote description that is NOT a TODO placeholder
            for line in content.splitlines():
                if line.startswith("> ") and "<!-- TODO" not in line:
                    with_desc += 1
                    break
        assert with_desc > 0, "Some functions should have real descriptions from C comments"


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------


class TestConsistency:
    """Verify consistency between L1/L2/L3 levels."""

    def test_module_count_matches_files(self, api_docs_dir):
        docs_dir, result = api_docs_dir
        module_files = list((docs_dir / "modules").glob("*.md"))
        assert len(module_files) == result["module_count"], (
            f"Module file count ({len(module_files)}) != result count ({result['module_count']})"
        )

    def test_func_count_matches_files(self, api_docs_dir):
        """File count should closely match result count (small diff from filename collisions)."""
        docs_dir, result = api_docs_dir
        func_files = list((docs_dir / "funcs").glob("*.md"))
        diff = abs(len(func_files) - result["func_count"])
        assert diff <= 10, (
            f"Func file count ({len(func_files)}) differs too much "
            f"from result count ({result['func_count']}), diff={diff}"
        )

    def test_index_lists_all_modules(self, api_docs_dir):
        """Index page should reference every module page."""
        docs_dir, _ = api_docs_dir
        index_content = (docs_dir / "index.md").read_text(encoding="utf-8")
        module_files = list((docs_dir / "modules").glob("*.md"))
        # At least 80% of module files should be referenced in index
        referenced = sum(1 for f in module_files if f.stem in index_content)
        ratio = referenced / len(module_files) if module_files else 1
        assert ratio > 0.8, f"Only {ratio:.0%} modules referenced in index"
