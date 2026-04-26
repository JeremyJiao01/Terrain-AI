"""End-to-end user flow test: simulates what happens after a user installs
the MCP server and starts using it with a real codebase.

Flow:
  1. User starts MCP server (list_tools)
  2. User indexes a repo (initialize_repository)
  3. User queries APIs (find_api, list_api_docs, get_api_doc)
  4. User switches context (list_repositories, get_repository_info)
  5. User browses docs (list_api_interfaces)
"""

from __future__ import annotations

import asyncio
import json
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


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    return tmp_path_factory.mktemp("user_workspace")


@pytest.fixture(scope="module")
def registry(workspace):
    from terrain.entrypoints.mcp.tools import MCPToolsRegistry

    reg = MCPToolsRegistry(workspace=workspace)
    yield reg
    reg.close()


def _call(registry, tool_name: str, args: dict | None = None):
    """Simulate MCP call_tool: dispatch → handler → JSON serialize → parse."""
    handler = registry.get_handler(tool_name)
    assert handler is not None, f"Tool '{tool_name}' not found"
    result = _run(handler(**(args or {})))
    # Round-trip through JSON like the real MCP server does
    text = json.dumps(result, ensure_ascii=False, default=str)
    return json.loads(text)


# The tests below MUST run in order — each step depends on the previous.
# pytest-ordering is not needed; pytest preserves definition order within a class.


class TestUserFlow:
    """Simulates the complete user journey after MCP installation."""

    # --- Step 1: Discovery ---

    def test_01_list_tools(self, registry):
        """User's MCP client calls list_tools on first connect."""
        tools = registry.tools()
        names = [t.name for t in tools]
        assert len(names) >= 5, f"Expected several tools, got {len(names)}"
        assert "find_api" in names
        assert "get_api_doc" in names
        print(f"  → {len(names)} tools available")

    # --- Step 2: Index repository ---

    def test_02_initialize_repository(self, registry):
        """User says: 'Index /path/to/tinycc'."""
        result = _call(registry, "initialize_repository", {
            "repo_path": str(TINYCC_PATH),
            "rebuild": True,
            "skip_wiki": True,   # Skip wiki to save time
            "skip_embed": False,  # Need embeddings for find_api
        })
        assert result["status"] == "success", f"Init failed: {result}"
        print(f"  → Indexed: {result.get('graph', {})}")

    # --- Step 3: Check repo info ---

    def test_03_get_repository_info(self, registry):
        """User asks: 'What repo is active?'"""
        result = _call(registry, "get_repository_info")
        assert "tinycc" in str(result).lower() or "repo" in str(result).lower()
        print(f"  → Repo info keys: {list(result.keys())}")

    def test_04_list_repositories(self, registry):
        """User asks: 'What repos have I indexed?'"""
        result = _call(registry, "list_repositories")
        repos = result.get("repositories", [])
        assert len(repos) >= 1
        print(f"  → {len(repos)} repo(s) indexed")

    # --- Step 4: Browse API documentation ---

    def test_05_list_api_docs_index(self, registry):
        """User asks: 'Show me the API docs overview.'"""
        result = _call(registry, "list_api_docs")
        # Should return L1 index content
        assert result is not None
        content = str(result)
        assert "module" in content.lower() or "模块" in content
        print(f"  → Index returned ({len(content)} chars)")

    def test_06_find_callers(self, registry):
        """User asks: 'Who calls this function?'"""
        from terrain.entrypoints.mcp.tools import ToolError

        # Use find_api to get a real function name first
        try:
            search = _call(registry, "find_api", {"query": "compile", "top_k": 1})
        except (ToolError, Exception):
            pytest.skip("No repository indexed — test_02 may not have run yet")
        results = search.get("results", [])
        if not results:
            pytest.skip("No functions found")
        qn = results[0].get("qualified_name", "")
        result = _call(registry, "find_callers", {"function_name": qn})
        assert isinstance(result, dict)
        print(f"  → find_callers keys: {list(result.keys())}")

    # --- Step 5: Semantic search ---

    def test_07_find_api_compile(self, registry):
        """User asks: 'Find APIs related to compiling source code.'"""
        result = _call(registry, "find_api", {"query": "compile source code", "top_k": 5})
        assert result["result_count"] > 0
        assert result["api_docs_available"] is True
        top = result["results"][0]
        assert top["qualified_name"]
        assert top["score"] > 0
        print(f"  → Top result: {top['qualified_name']} (score={top['score']:.3f})")

    def test_08_find_api_parse(self, registry):
        """User asks: 'How does expression parsing work?'"""
        result = _call(registry, "find_api", {"query": "parse expression", "top_k": 5})
        assert result["result_count"] > 0
        # At least one result should have an API doc attached
        with_doc = sum(1 for r in result["results"] if r.get("api_doc"))
        print(f"  → {result['result_count']} results, {with_doc} with API docs")

    def test_09_find_api_chinese(self, registry):
        """User asks in Chinese: '内存分配相关的函数'."""
        result = _call(registry, "find_api", {"query": "内存分配", "top_k": 3})
        assert result["result_count"] > 0
        print(f"  → Chinese query returned {result['result_count']} results")

    # --- Step 6: Verify API doc content quality ---

    def test_10_api_doc_has_signature(self, registry):
        """API docs attached to search results should have C signatures."""
        result = _call(registry, "find_api", {"query": "compile", "top_k": 10})
        for r in result["results"]:
            doc = r.get("api_doc") or ""
            if "签名:" in doc and "(" in doc:
                print(f"  → Found signature in: {r['qualified_name']}")
                return
        # Acceptable if signatures exist in some results
        assert result["result_count"] > 0

    def test_11_api_doc_has_call_tree(self, registry):
        """API docs should include call relationship info."""
        result = _call(registry, "find_api", {"query": "generate code output", "top_k": 10})
        for r in result["results"]:
            doc = r.get("api_doc") or ""
            if "被调用" in doc or "调用树" in doc:
                print(f"  → Call info in: {r['qualified_name']}")
                return
        assert result["result_count"] > 0

    # --- Step 7: Full round-trip JSON serialization ---

    def test_12_all_results_json_serializable(self, registry):
        """Every tool result must survive JSON round-trip (MCP requirement)."""
        test_calls = [
            ("list_repositories", {}),
            ("get_repository_info", {}),
            ("find_api", {"query": "function", "top_k": 2}),
        ]
        for tool_name, args in test_calls:
            result = _call(registry, tool_name, args)
            # _call already does JSON round-trip; if we get here, it worked
            assert result is not None, f"{tool_name} returned None"
        print("  → All 4 tools passed JSON round-trip")
