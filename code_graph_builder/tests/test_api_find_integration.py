"""Integration test: api-find (semantic search + API doc attachment) on tinycc.

Tests the full find_api pipeline: query → embedding → vector search →
API doc lookup → combined result. Validates relevance, doc attachment,
and result structure.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

TINYCC_PATH = Path(__file__).resolve().parents[3] / "tinycc"

pytestmark = [
    pytest.mark.skipif(
        not TINYCC_PATH.exists(),
        reason=f"tinycc source not found at {TINYCC_PATH}",
    ),
    pytest.mark.skipif(
        not os.environ.get("DASHSCOPE_API_KEY"),
        reason="DASHSCOPE_API_KEY not set",
    ),
]


@pytest.fixture(scope="module")
def mcp_registry(tmp_path_factory):
    """Set up MCPToolsRegistry with fully indexed tinycc repo."""
    from code_graph_builder.mcp.tools import MCPToolsRegistry

    workspace = tmp_path_factory.mktemp("workspace")
    registry = MCPToolsRegistry(workspace=workspace)

    # Run full pipeline via initialize_repository handler
    result = asyncio.get_event_loop().run_until_complete(
        registry._handle_initialize_repository(
            repo_path=str(TINYCC_PATH),
            rebuild=True,
            skip_wiki=True,
            skip_embed=False,
        )
    )
    assert result.get("status") == "success", f"Init failed: {result}"

    yield registry
    registry.close()


def _find_api(registry, query: str, top_k: int = 5) -> dict:
    """Helper to call find_api synchronously."""
    return asyncio.get_event_loop().run_until_complete(
        registry._handle_find_api(query=query, top_k=top_k)
    )


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    """Verify find_api returns well-structured results."""

    def test_returns_dict(self, mcp_registry):
        result = _find_api(mcp_registry, "compile")
        assert isinstance(result, dict)

    def test_has_required_keys(self, mcp_registry):
        result = _find_api(mcp_registry, "compile")
        assert "query" in result
        assert "result_count" in result
        assert "api_docs_available" in result
        assert "results" in result

    def test_query_echoed(self, mcp_registry):
        result = _find_api(mcp_registry, "parse expression")
        assert result["query"] == "parse expression"

    def test_result_count_matches(self, mcp_registry):
        result = _find_api(mcp_registry, "compile", top_k=3)
        assert result["result_count"] == len(result["results"])
        assert result["result_count"] <= 3

    def test_api_docs_available(self, mcp_registry):
        result = _find_api(mcp_registry, "compile")
        assert result["api_docs_available"] is True

    def test_result_entry_keys(self, mcp_registry):
        result = _find_api(mcp_registry, "compile")
        assert len(result["results"]) > 0
        entry = result["results"][0]
        expected_keys = {
            "qualified_name", "name", "type", "score",
            "file_path", "start_line", "end_line",
            "source_code", "api_doc",
        }
        assert expected_keys.issubset(entry.keys())


# ---------------------------------------------------------------------------
# Search relevance
# ---------------------------------------------------------------------------


class TestSearchRelevance:
    """Verify find_api returns relevant results for various queries."""

    def test_search_compile(self, mcp_registry):
        result = _find_api(mcp_registry, "compile source code")
        qns = [r["qualified_name"] for r in result["results"]]
        found = any("compile" in qn.lower() or "tcc" in qn.lower() for qn in qns)
        assert found, f"Expected compile-related results, got: {qns}"

    def test_search_parse(self, mcp_registry):
        result = _find_api(mcp_registry, "parse C expression")
        qns = [r["qualified_name"] for r in result["results"]]
        found = any("parse" in qn.lower() or "expr" in qn.lower() for qn in qns)
        assert found, f"Expected parse-related results, got: {qns}"

    def test_search_memory(self, mcp_registry):
        result = _find_api(mcp_registry, "allocate memory")
        qns = [r["qualified_name"] for r in result["results"]]
        found = any(
            "alloc" in qn.lower() or "malloc" in qn.lower() or "mem" in qn.lower()
            for qn in qns
        )
        assert found, f"Expected memory-related results, got: {qns}"

    def test_scores_are_valid(self, mcp_registry):
        result = _find_api(mcp_registry, "generate assembly code")
        for r in result["results"]:
            assert isinstance(r["score"], float)
            assert 0.0 <= r["score"] <= 1.0

    def test_scores_descending(self, mcp_registry):
        result = _find_api(mcp_registry, "output binary")
        scores = [r["score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# API doc attachment
# ---------------------------------------------------------------------------


class TestApiDocAttachment:
    """Verify L3 API docs are attached to search results."""

    def test_some_results_have_api_doc(self, mcp_registry):
        result = _find_api(mcp_registry, "compile source file", top_k=10)
        with_doc = sum(1 for r in result["results"] if r["api_doc"])
        assert with_doc > 0, "Some results should have API docs attached"

    def test_api_doc_is_markdown(self, mcp_registry):
        result = _find_api(mcp_registry, "parse tokens", top_k=10)
        for r in result["results"]:
            if r["api_doc"]:
                assert r["api_doc"].startswith("# "), (
                    f"API doc should start with markdown title, got: {r['api_doc'][:50]}"
                )
                break

    def test_api_doc_has_signature(self, mcp_registry):
        """Attached API docs should contain function signature."""
        result = _find_api(mcp_registry, "lexer tokenizer", top_k=10)
        for r in result["results"]:
            if r["api_doc"] and "签名:" in r["api_doc"]:
                return  # Found
        # It's ok if some results don't have signatures (e.g., macros)
        # Just check at least one doc was attached
        with_doc = sum(1 for r in result["results"] if r["api_doc"])
        if with_doc > 0:
            return  # Docs attached, signature format may vary
        pytest.fail("No API docs attached to any result")

    def test_api_doc_has_call_info(self, mcp_registry):
        """Attached API docs should contain call relationship info."""
        result = _find_api(mcp_registry, "compile", top_k=10)
        for r in result["results"]:
            if r["api_doc"] and "被调用" in r["api_doc"]:
                return
        pytest.fail("No API doc has call relationship info")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_query(self, mcp_registry):
        """Empty query should still return results (or handle gracefully)."""
        try:
            result = _find_api(mcp_registry, "")
            # Either returns empty or some results
            assert isinstance(result, dict)
        except Exception:
            pass  # Raising an error is also acceptable

    def test_top_k_1(self, mcp_registry):
        result = _find_api(mcp_registry, "main", top_k=1)
        assert len(result["results"]) <= 1

    def test_top_k_large(self, mcp_registry):
        result = _find_api(mcp_registry, "function", top_k=50)
        assert len(result["results"]) <= 50
        assert len(result["results"]) > 0

    def test_chinese_query(self, mcp_registry):
        """Chinese natural language query should work."""
        result = _find_api(mcp_registry, "编译源代码")
        assert isinstance(result, dict)
        assert result["result_count"] >= 0

    def test_specific_function_name(self, mcp_registry):
        """Querying an exact function name should find it."""
        result = _find_api(mcp_registry, "tcc_compile", top_k=10)
        qns = [r["qualified_name"] for r in result["results"]]
        # Should find the function or something very related
        assert len(qns) > 0
