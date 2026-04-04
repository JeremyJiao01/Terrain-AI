"""Tests for GBK/GB2312 encoding support in C file parsing.

Verifies that the parser correctly extracts functions and structs from
C source files encoded in GBK, GB2312, and UTF-8, and that the results
are consistent across encodings.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Skip the entire module if fixture files are missing
pytestmark = pytest.mark.skipif(
    not (FIXTURES_DIR / "test_gbk.c").exists(),
    reason="GBK fixture files not found; run create_gbk_fixtures.py first",
)

# Expected function names per fixture file.
# Note: for functions returning pointer types (e.g. void*), the parser may
# store the name as "* func_name(params)" instead of just "func_name".
GBK_EXPECTED_FUNCTIONS = {"add", "print_welcome", "get_age"}
GB2312_EXPECTED_FUNCTIONS = {"alloc_buffer", "free_buffer"}
ALL_EXPECTED_FUNCTIONS = GBK_EXPECTED_FUNCTIONS | GB2312_EXPECTED_FUNCTIONS
GBK_EXPECTED_STRUCTS = {"UserInfo"}


def _build_graph_for_dir(project_dir: Path):
    """Build a memory-backed graph for *project_dir* and return the builder."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(
        repo_path=str(project_dir),
        backend="memory",
    )
    builder.build_graph(clean=True)
    return builder


def _extract_node_names(builder, label: str) -> set[str]:
    """Return a set of node ``name`` values for the given label."""
    ingestor = builder._get_ingestor()
    data = ingestor.export_graph()
    names: set[str] = set()
    for node in data.get("nodes", []):
        if node.get("label") == label:
            name = node.get("properties", {}).get("name")
            if name:
                names.add(name)
    return names


def _function_name_found(expected: str, actual_names: set[str]) -> bool:
    """Check if *expected* appears in *actual_names*.

    The parser may store pointer-returning functions as ``"* func(params)"``
    rather than plain ``"func"``, so we also check whether any actual name
    contains the expected identifier.
    """
    if expected in actual_names:
        return True
    # Fallback: check for "* expected" or names containing the identifier
    return any(expected in n for n in actual_names)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gbk_project(tmp_path_factory) -> Path:
    """Create a temporary project containing only the GBK-encoded C file."""
    project = tmp_path_factory.mktemp("gbk_project")
    shutil.copy(FIXTURES_DIR / "test_gbk.c", project / "test_gbk.c")
    return project


@pytest.fixture(scope="module")
def gb2312_project(tmp_path_factory) -> Path:
    """Create a temporary project containing only the GB2312-encoded C file."""
    project = tmp_path_factory.mktemp("gb2312_project")
    shutil.copy(FIXTURES_DIR / "test_gb2312.c", project / "test_gb2312.c")
    return project


@pytest.fixture(scope="module")
def utf8_project(tmp_path_factory) -> Path:
    """Create a temporary project containing only the UTF-8-encoded C file."""
    project = tmp_path_factory.mktemp("utf8_project")
    shutil.copy(FIXTURES_DIR / "test_utf8.c", project / "test_utf8.c")
    return project


@pytest.fixture(scope="module")
def combined_project(tmp_path_factory) -> Path:
    """Create a temporary project containing both GBK and GB2312 C files."""
    project = tmp_path_factory.mktemp("combined_project")
    shutil.copy(FIXTURES_DIR / "test_gbk.c", project / "test_gbk.c")
    shutil.copy(FIXTURES_DIR / "test_gb2312.c", project / "test_gb2312.c")
    return project


# ---------------------------------------------------------------------------
# Tests — GBK encoded file
# ---------------------------------------------------------------------------

class TestGBKParsing:
    """Verify function and struct extraction from a GBK-encoded C file."""

    def test_functions_detected(self, gbk_project: Path):
        builder = _build_graph_for_dir(gbk_project)
        functions = _extract_node_names(builder, "Function")
        for name in GBK_EXPECTED_FUNCTIONS:
            assert _function_name_found(name, functions), (
                f"Function '{name}' not found in GBK parse. Got: {functions}"
            )

    def test_struct_detected(self, gbk_project: Path):
        builder = _build_graph_for_dir(gbk_project)
        classes = _extract_node_names(builder, "Class")
        for name in GBK_EXPECTED_STRUCTS:
            assert name in classes, (
                f"Struct '{name}' not found in GBK parse. Got: {classes}"
            )

    def test_no_garbled_names(self, gbk_project: Path):
        builder = _build_graph_for_dir(gbk_project)
        ingestor = builder._get_ingestor()
        data = ingestor.export_graph()
        for node in data.get("nodes", []):
            name = node.get("properties", {}).get("name", "")
            # Function/struct names should be pure ASCII identifiers
            if node.get("label") in ("Function", "Class"):
                assert name.isascii(), (
                    f"Non-ASCII name detected (possible garbled encoding): {name!r}"
                )


# ---------------------------------------------------------------------------
# Tests — GB2312 encoded file
# ---------------------------------------------------------------------------

class TestGB2312Parsing:
    """Verify function extraction from a GB2312-encoded C file."""

    def test_functions_detected(self, gb2312_project: Path):
        builder = _build_graph_for_dir(gb2312_project)
        functions = _extract_node_names(builder, "Function")
        for name in GB2312_EXPECTED_FUNCTIONS:
            assert _function_name_found(name, functions), (
                f"Function '{name}' not found in GB2312 parse. Got: {functions}"
            )


# ---------------------------------------------------------------------------
# Tests — UTF-8 vs GBK consistency
# ---------------------------------------------------------------------------

class TestEncodingConsistency:
    """Verify that GBK and UTF-8 produce identical parse results."""

    def test_same_functions(self, gbk_project: Path, utf8_project: Path):
        gbk_funcs = _extract_node_names(_build_graph_for_dir(gbk_project), "Function")
        utf8_funcs = _extract_node_names(_build_graph_for_dir(utf8_project), "Function")
        # Both encodings should detect the same set of function identifiers
        assert len(gbk_funcs) == len(utf8_funcs), (
            f"GBK found {len(gbk_funcs)} functions, UTF-8 found {len(utf8_funcs)}. "
            f"GBK: {gbk_funcs}, UTF-8: {utf8_funcs}"
        )
        for name in GBK_EXPECTED_FUNCTIONS:
            assert _function_name_found(name, gbk_funcs), (
                f"'{name}' missing from GBK results: {gbk_funcs}"
            )
            assert _function_name_found(name, utf8_funcs), (
                f"'{name}' missing from UTF-8 results: {utf8_funcs}"
            )

    def test_same_structs(self, gbk_project: Path, utf8_project: Path):
        gbk_classes = _extract_node_names(_build_graph_for_dir(gbk_project), "Class")
        utf8_classes = _extract_node_names(_build_graph_for_dir(utf8_project), "Class")
        assert gbk_classes == utf8_classes, (
            f"GBK classes {gbk_classes} differ from UTF-8 classes {utf8_classes}"
        )


# ---------------------------------------------------------------------------
# Tests — Combined project (both GBK + GB2312 files)
# ---------------------------------------------------------------------------

class TestCombinedEncodings:
    """Verify that a project with mixed-encoding files parses correctly."""

    def test_all_functions_detected(self, combined_project: Path):
        builder = _build_graph_for_dir(combined_project)
        functions = _extract_node_names(builder, "Function")
        for name in ALL_EXPECTED_FUNCTIONS:
            assert _function_name_found(name, functions), (
                f"Function '{name}' not found in combined parse. Got: {functions}"
            )

    def test_struct_detected(self, combined_project: Path):
        builder = _build_graph_for_dir(combined_project)
        classes = _extract_node_names(builder, "Class")
        for name in GBK_EXPECTED_STRUCTS:
            assert name in classes, (
                f"Struct '{name}' not found in combined parse. Got: {classes}"
            )
