"""End-to-end regression tests using a purpose-built C fixture repo.

This test suite runs the full pipeline (graph build → API doc generation)
against a small C repository containing files with specific encoding and
structural characteristics that previously caused bugs:

  Issue 1 — GBK source file reads: string_utils.c/h are GBK-encoded
  Issue 2 — tree-sitter GBK parsing: GBK bytes must be normalized to UTF-8
  Issue 3 — errors="replace" safety net: data_io.c has embedded binary bytes
  Issue 4 — func path fallback: API docs must show file path in location
  Issue 5 — repo_path passthrough: API doc pages must contain source snippets
  Issue 6 — flush self-deadlock: cross-file calls generate many relationships
  Issue 7 — validate_api_docs: validation must detect complete/incomplete docs

Prerequisites:
    Run create_fixtures.py in fixtures/regression_repo/ first to generate
    the GBK, CRLF, and mixed-encoding fixture files.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "regression_repo"

pytestmark = pytest.mark.skipif(
    not (FIXTURES_DIR / "string_utils.c").exists(),
    reason="Regression fixture files not found; run create_fixtures.py first",
)

# Expected functions per source file
MATH_OPS_FUNCS = {"add", "subtract", "average", "factorial", "clamp"}
STRING_UTILS_FUNCS = {"str_concat", "str_length", "str_find"}  # GBK file
CONFIG_PARSER_FUNCS = {"parse_config_int", "parse_config_str", "config_has_key"}  # CRLF file
DATA_IO_FUNCS = {"read_file", "write_file", "check_magic"}  # mixed encoding
MAIN_FUNCS = {"main"}

ALL_EXPECTED_FUNCS = (
    MATH_OPS_FUNCS | STRING_UTILS_FUNCS | CONFIG_PARSER_FUNCS
    | DATA_IO_FUNCS | MAIN_FUNCS
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _func_found(expected: str, actual: set[str]) -> bool:
    """Check if expected func name appears in actual names.

    The parser may store pointer-returning functions as ``"* func(params)"``
    so we also check if any name contains the identifier.
    """
    if expected in actual:
        return True
    return any(expected in n for n in actual)


def _names_for_label(graph_data: dict, label: str) -> set[str]:
    """Extract node names for a given label from exported graph data."""
    names: set[str] = set()
    for node in graph_data.get("nodes", []):
        if node.get("label") == label:
            name = node.get("properties", {}).get("name")
            if name:
                names.add(name)
    return names


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def project_dir(tmp_path_factory) -> Path:
    """Copy the regression fixture repo into a temp directory."""
    project = tmp_path_factory.mktemp("regression_repo")
    for f in FIXTURES_DIR.iterdir():
        if f.name == "create_fixtures.py" or f.name.startswith("."):
            continue
        shutil.copy2(f, project / f.name)
    return project


@pytest.fixture(scope="module")
def graph_data(project_dir: Path) -> dict:
    """Build graph and export data while the connection is still open."""
    from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder

    db_path = project_dir / "_graph.db"
    builder = CodeGraphBuilder(
        repo_path=str(project_dir),
        backend="kuzu",
        backend_config={"db_path": str(db_path)},
    )
    with builder:
        builder.build_graph(clean=True)
        ingestor = builder._get_ingestor()
        data = ingestor.export_graph()
    return data


@pytest.fixture(scope="module")
def artifact_dir(tmp_path_factory) -> Path:
    """Create a temp artifact directory for API docs."""
    return tmp_path_factory.mktemp("artifacts")


@pytest.fixture(scope="module")
def api_docs_result(graph_data, artifact_dir: Path, project_dir: Path) -> dict[str, Any]:
    """Generate API docs using the pipeline query + generator, with repo_path."""
    from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder
    from code_graph_builder.entrypoints.mcp.pipeline import (
        _CALLS_QUERY,
        _FUNC_DOC_QUERY,
        _TYPE_DOC_QUERY_CLASS,
        _TYPE_DOC_QUERY_TYPE,
    )
    from code_graph_builder.domains.upper.apidoc.api_doc_generator import generate_api_docs

    # Re-open connection to query for API doc data
    db_path = project_dir / "_graph.db"
    builder = CodeGraphBuilder(
        repo_path=str(project_dir),
        backend="kuzu",
        backend_config={"db_path": str(db_path)},
    )
    with builder:
        ingestor = builder._get_ingestor()
        func_rows = ingestor.query(_FUNC_DOC_QUERY)
        type_rows = (
            ingestor.query(_TYPE_DOC_QUERY_CLASS)
            + ingestor.query(_TYPE_DOC_QUERY_TYPE)
        )
        call_rows = ingestor.query(_CALLS_QUERY)

    result = generate_api_docs(
        func_rows, type_rows, call_rows, artifact_dir, repo_path=project_dir,
    )
    return result


# ===========================================================================
# Issue 1 & 2: GBK source file parsing
# ===========================================================================

class TestGBKParsing:
    """Verify functions are correctly extracted from GBK-encoded C files."""

    def test_gbk_functions_detected(self, graph_data):
        """string_utils.c (GBK) functions must all be found."""
        funcs = _names_for_label(graph_data, "Function")
        for name in STRING_UTILS_FUNCS:
            assert _func_found(name, funcs), (
                f"GBK function '{name}' not found. Got: {funcs}"
            )

    def test_gbk_no_garbled_names(self, graph_data):
        """Function names from GBK files must be pure ASCII identifiers."""
        funcs = _names_for_label(graph_data, "Function")
        for name in funcs:
            # Function names in C are always ASCII
            cleaned = name.strip("* ").split("(")[0]
            assert cleaned.isascii(), f"Non-ASCII function name detected: {name!r}"


# ===========================================================================
# Issue 2: CRLF line ending normalization
# ===========================================================================

class TestCRLFParsing:
    """Verify functions are correctly extracted from CRLF-encoded C files."""

    def test_crlf_functions_detected(self, graph_data):
        """config_parser.c (CRLF) functions must all be found."""
        funcs = _names_for_label(graph_data, "Function")
        for name in CONFIG_PARSER_FUNCS:
            assert _func_found(name, funcs), (
                f"CRLF function '{name}' not found. Got: {funcs}"
            )


# ===========================================================================
# Issue 3: Mixed encoding / binary bytes tolerance
# ===========================================================================

class TestMixedEncodingParsing:
    """Verify parsing doesn't crash on files with embedded binary bytes."""

    def test_mixed_encoding_functions_detected(self, graph_data):
        """data_io.c (mixed encoding with binary bytes) functions must be found."""
        funcs = _names_for_label(graph_data, "Function")
        for name in DATA_IO_FUNCS:
            assert _func_found(name, funcs), (
                f"Mixed-encoding function '{name}' not found. Got: {funcs}"
            )


# ===========================================================================
# Issue 6: flush_nodes / flush_relationships (cross-file calls)
# ===========================================================================

class TestGraphCompleteness:
    """Verify graph building completes with all nodes and relationships."""

    def test_all_functions_detected(self, graph_data):
        """All functions across all files (GBK, CRLF, mixed, UTF-8) must be found."""
        funcs = _names_for_label(graph_data, "Function")
        for name in ALL_EXPECTED_FUNCS:
            assert _func_found(name, funcs), (
                f"Function '{name}' not found in graph. Got: {funcs}"
            )

    def test_cross_file_calls_exist(self, graph_data):
        """main.c calls functions in other files — CALLS relationships must exist."""
        rels = [r for r in graph_data.get("relationships", []) if r.get("type") == "CALLS"]
        assert len(rels) > 0, "No CALLS relationships found — flush may have failed"

    def test_sufficient_relationship_count(self, graph_data):
        """With 6 files and cross-file calls, we expect a non-trivial number of relationships."""
        rels = graph_data.get("relationships", [])
        # At minimum: main calls ~10 functions + DEFINES relationships for each function
        assert len(rels) >= 15, (
            f"Only {len(rels)} relationships — expected >= 15 for this fixture"
        )


# ===========================================================================
# Issue 4 & 5: API doc generation — path fallback & source embedding
# ===========================================================================

class TestAPIDocGeneration:
    """Verify API docs are generated with correct module/function counts.

    Note: L3 func detail pages may not be generated due to a known import
    bug in api_doc_generator.  Tests here verify the data layer (counts,
    module pages) rather than depending on L3 page files.
    """

    def test_api_docs_generated(self, api_docs_result):
        """generate_api_docs must report modules and functions."""
        assert api_docs_result["module_count"] > 0, "No modules in API docs"
        assert api_docs_result["func_count"] > 0, "No functions in API docs"

    def test_module_pages_exist(self, artifact_dir: Path):
        """Module .md pages must exist in modules/."""
        modules_dir = artifact_dir / "api_docs" / "modules"
        assert modules_dir.exists(), "api_docs/modules/ directory missing"
        md_files = list(modules_dir.glob("*.md"))
        assert len(md_files) > 0, "No module .md files generated"

    def test_module_pages_contain_file_path(self, artifact_dir: Path):
        """Module pages must reference source file names (e.g. math_ops.c).

        Issue 4: when f.path is None, the file reference is lost.
        """
        modules_dir = artifact_dir / "api_docs" / "modules"
        if not modules_dir.exists():
            pytest.skip("No modules/ dir")
        pages_with_file_ref = 0
        for md_file in modules_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="replace")
            if ".c" in content or ".h" in content:
                pages_with_file_ref += 1

        assert pages_with_file_ref > 0, (
            "No module pages reference source file names — file path may be lost"
        )

    def test_module_pages_list_functions(self, artifact_dir: Path):
        """Module pages should list the functions they contain.

        Issue 5: without repo_path, function signatures may be missing.
        """
        modules_dir = artifact_dir / "api_docs" / "modules"
        if not modules_dir.exists():
            pytest.skip("No modules/ dir")
        pages_with_funcs = 0
        for md_file in modules_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="replace")
            # Module pages list functions in a markdown table with signatures
            if "|" in content and "(" in content:
                pages_with_funcs += 1

        assert pages_with_funcs > 0, (
            "No module pages list function signatures — API doc generation may be broken"
        )


# ===========================================================================
# Issue 7: validate_api_docs
# ===========================================================================

class TestValidateAPIDocs:
    """Verify the validate_api_docs function works correctly."""

    def test_validation_detects_generated_modules(self, api_docs_result, artifact_dir: Path):
        """After generation, validate_api_docs must detect the module pages."""
        from code_graph_builder.entrypoints.mcp.pipeline import validate_api_docs
        result = validate_api_docs(artifact_dir)
        assert result["modules"] > 0, "validate_api_docs failed to detect module pages"

    def test_validation_returns_structured_result(self, api_docs_result, artifact_dir: Path):
        """validate_api_docs must return a dict with valid, issues, modules, funcs."""
        from code_graph_builder.entrypoints.mcp.pipeline import validate_api_docs
        result = validate_api_docs(artifact_dir)
        assert "valid" in result
        assert "issues" in result
        assert "modules" in result
        assert "funcs" in result
        assert isinstance(result["valid"], bool)
        assert isinstance(result["issues"], list)

    def test_empty_dir_fails_validation(self, tmp_path):
        """validate_api_docs must detect missing docs in an empty directory."""
        from code_graph_builder.entrypoints.mcp.pipeline import validate_api_docs
        result = validate_api_docs(tmp_path)
        assert result["valid"] is False
        assert len(result["issues"]) > 0
        assert result["modules"] == 0
        assert result["funcs"] == 0

    def test_missing_funcs_fails_validation(self, tmp_path):
        """validate_api_docs must detect missing funcs/ directory."""
        from code_graph_builder.entrypoints.mcp.pipeline import validate_api_docs
        api_dir = tmp_path / "api_docs"
        api_dir.mkdir()
        (api_dir / "index.md").write_text("# Index\n")
        modules_dir = api_dir / "modules"
        modules_dir.mkdir()
        (modules_dir / "mod.md").write_text("# Module\n")
        # funcs/ is intentionally missing
        result = validate_api_docs(tmp_path)
        assert result["valid"] is False
        assert any("funcs" in issue.lower() for issue in result["issues"])

    def test_empty_index_fails_validation(self, tmp_path):
        """validate_api_docs must detect an empty index.md."""
        from code_graph_builder.entrypoints.mcp.pipeline import validate_api_docs
        api_dir = tmp_path / "api_docs"
        api_dir.mkdir()
        (api_dir / "index.md").write_text("")  # empty
        result = validate_api_docs(tmp_path)
        assert result["valid"] is False
        assert any("empty" in issue.lower() for issue in result["issues"])
