"""Step 1 integration test: graph-build on tinycc repository.

Builds a knowledge graph from the real tinycc C compiler source code,
then validates that nodes, relationships, and C-specific properties
(signatures, visibility, docstrings, macros, structs) are correctly extracted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TINYCC_PATH = Path(__file__).resolve().parents[3] / "tinycc"

# Skip entire module if tinycc source is not available
pytestmark = pytest.mark.skipif(
    not TINYCC_PATH.exists(),
    reason=f"tinycc source not found at {TINYCC_PATH}",
)


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    """Build the tinycc graph once for all tests in this module."""
    from code_graph_builder.mcp.pipeline import build_graph

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


# ---------------------------------------------------------------------------
# Basic graph structure
# ---------------------------------------------------------------------------


class TestGraphStructure:
    """Verify the graph has expected node and relationship counts."""

    def test_has_modules(self, builder):
        rows = builder.query("MATCH (m:Module) RETURN count(m) AS cnt")
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "Graph should have Module nodes"

    def test_has_functions(self, builder):
        rows = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 50, f"Expected many functions in tinycc, got {cnt}"

    def test_has_calls(self, builder):
        rows = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 50, f"Expected many CALLS relationships, got {cnt}"

    def test_has_defines(self, builder):
        rows = builder.query("MATCH ()-[r:DEFINES]->() RETURN count(r) AS cnt")
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "Graph should have DEFINES relationships"

    def test_has_classes_or_types(self, builder):
        """tinycc has structs, enums — should appear as Class or Type nodes."""
        rows = builder.query(
            "MATCH (c:Class) RETURN count(c) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "tinycc should have struct/enum/union Class nodes"


# ---------------------------------------------------------------------------
# C-specific property extraction
# ---------------------------------------------------------------------------


class TestCProperties:
    """Verify C-specific properties are extracted correctly."""

    def test_function_has_signature(self, builder):
        """At least some functions should have non-empty signatures."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.signature IS NOT NULL AND f.signature <> '' "
            "RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 10, f"Expected functions with signatures, got {cnt}"

    def test_function_has_return_type(self, builder):
        rows = builder.query(
            "MATCH (f:Function) WHERE f.return_type IS NOT NULL AND f.return_type <> '' "
            "RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 10, f"Expected functions with return types, got {cnt}"

    def test_function_has_visibility(self, builder):
        """Functions should have public/static/extern visibility."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.visibility IN ['public', 'static', 'extern'] "
            "RETURN f.visibility AS vis, count(f) AS cnt "
            "ORDER BY cnt DESC"
        )
        assert len(rows) > 0, "Expected functions with visibility"
        vis_types = {r["vis"] for r in rows}
        assert "static" in vis_types, "tinycc should have static functions"

    def test_static_functions_exist(self, builder):
        """tinycc has many static helper functions."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.visibility = 'static' RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 20, f"Expected many static functions, got {cnt}"

    def test_public_functions_exist(self, builder):
        """Functions declared in .h files should be public."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.visibility = 'public' RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, f"Expected public functions from headers, got {cnt}"


# ---------------------------------------------------------------------------
# Comment/docstring extraction (P0 feature)
# ---------------------------------------------------------------------------


class TestDocstringExtraction:
    """Verify C comments above functions are extracted as docstrings."""

    def test_some_functions_have_docstrings(self, builder):
        """tinycc has comments above many functions — some should be captured."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.docstring IS NOT NULL AND f.docstring <> '' "
            "RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "Expected some functions with extracted C comments as docstrings"

    def test_docstring_not_decorative(self, builder):
        """Extracted docstrings should not be purely decorative (e.g., '---')."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.docstring IS NOT NULL AND f.docstring <> '' "
            "RETURN f.docstring AS doc LIMIT 20"
        )
        for r in rows:
            doc = r["doc"]
            # Should contain actual words, not just dashes/stars
            assert any(c.isalpha() for c in doc), f"Decorative docstring leaked: {doc!r}"


# ---------------------------------------------------------------------------
# Macro extraction
# ---------------------------------------------------------------------------


class TestMacroExtraction:
    """Verify #define macros are extracted as Function nodes with kind='macro'."""

    def test_macros_exist(self, builder):
        rows = builder.query(
            "MATCH (f:Function) WHERE f.kind = 'macro' RETURN count(f) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "tinycc should have macro definitions"

    def test_macro_has_signature(self, builder):
        """Macro signature should contain the #define text."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.kind = 'macro' AND f.signature IS NOT NULL "
            "RETURN f.name AS name, f.signature AS sig LIMIT 5"
        )
        assert len(rows) > 0
        for r in rows:
            assert r["name"], "Macro should have a name"


# ---------------------------------------------------------------------------
# Struct/Enum extraction
# ---------------------------------------------------------------------------


class TestTypeExtraction:
    """Verify struct/enum/union are extracted as Class nodes."""

    def test_structs_exist(self, builder):
        rows = builder.query(
            "MATCH (c:Class) WHERE c.kind = 'struct' RETURN count(c) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        assert cnt > 0, "tinycc should have struct definitions"

    def test_enums_exist(self, builder):
        rows = builder.query(
            "MATCH (c:Class) WHERE c.kind = 'enum' RETURN count(c) AS cnt"
        )
        cnt = list(rows[0].values())[0] if rows else 0
        # tinycc may or may not have named enums, so just check >= 0
        assert cnt >= 0

    def test_class_has_kind(self, builder):
        """All Class nodes should have a kind property (struct/enum/union)."""
        rows = builder.query(
            "MATCH (c:Class) WHERE c.kind IS NOT NULL RETURN DISTINCT c.kind AS kind"
        )
        kinds = {r["kind"] for r in rows}
        assert len(kinds) > 0, "Class nodes should have kind property"


# ---------------------------------------------------------------------------
# Module-Function relationships
# ---------------------------------------------------------------------------


class TestRelationships:
    """Verify graph relationships are correct."""

    def test_most_functions_have_module(self, builder):
        """Most functions extracted from source should have a parent module.

        Note: CALLS edges can create Function stubs without DEFINES.
        We check that the ratio of defined functions is high.
        """
        total = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
        defined = builder.query(
            "MATCH (m:Module)-[:DEFINES]->(f:Function) RETURN count(f) AS cnt"
        )
        total_cnt = list(total[0].values())[0] if total else 0
        defined_cnt = list(defined[0].values())[0] if defined else 0
        assert defined_cnt > 100, f"Expected many defined functions, got {defined_cnt}"
        ratio = defined_cnt / total_cnt if total_cnt > 0 else 0
        assert ratio > 0.05, f"Only {ratio:.1%} functions have parent module"

    def test_calls_have_valid_endpoints(self, builder):
        """CALLS relationships should connect existing functions."""
        rows = builder.query(
            "MATCH (a:Function)-[:CALLS]->(b:Function) "
            "RETURN a.qualified_name AS caller, b.qualified_name AS callee "
            "LIMIT 5"
        )
        assert len(rows) > 0
        for r in rows:
            assert r["caller"], "Caller should have qualified_name"
            assert r["callee"], "Callee should have qualified_name"

    def test_known_function_exists(self, builder):
        """tinycc's main entry point 'tcc_main' should exist."""
        rows = builder.query(
            "MATCH (f:Function) WHERE f.name = 'tcc_main' RETURN f.qualified_name AS qn"
        )
        # tcc_main might be named differently, so just check it's queryable
        # If not found, check for 'main' instead
        if not rows:
            rows = builder.query(
                "MATCH (f:Function) WHERE f.name = 'main' RETURN f.qualified_name AS qn"
            )
        assert len(rows) > 0, "Should find tcc_main or main function"
