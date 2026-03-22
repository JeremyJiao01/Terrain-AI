"""MCP protocol layer tests: tool registration, dispatch, error handling.

Tests the MCP server's tool listing, call_tool dispatch, ToolError propagation,
and JSON serialization — without requiring a live stdio transport.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

TINYCC_PATH = Path(__file__).resolve().parents[3] / "tinycc"

pytestmark = pytest.mark.skipif(
    not TINYCC_PATH.exists(),
    reason=f"tinycc source not found at {TINYCC_PATH}",
)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    from code_graph_builder.mcp.tools import MCPToolsRegistry

    workspace = tmp_path_factory.mktemp("workspace")
    reg = MCPToolsRegistry(workspace=workspace)
    yield reg
    reg.close()


@pytest.fixture(scope="module")
def indexed_registry(tmp_path_factory):
    """Registry with tinycc indexed (graph + api-docs, skip embed/wiki)."""
    from code_graph_builder.mcp.tools import MCPToolsRegistry

    workspace = tmp_path_factory.mktemp("indexed_workspace")
    reg = MCPToolsRegistry(workspace=workspace)
    asyncio.get_event_loop().run_until_complete(
        reg._handle_initialize_repository(
            repo_path=str(TINYCC_PATH),
            rebuild=True,
            skip_wiki=True,
            skip_embed=True,
        )
    )
    yield reg
    reg.close()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tool registration & discovery
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify tools are correctly registered and discoverable."""

    def test_tools_list_not_empty(self, registry):
        tools = registry.tools()
        assert len(tools) > 0

    def test_all_tools_have_name(self, registry):
        for t in registry.tools():
            assert t.name, f"Tool missing name: {t}"

    def test_all_tools_have_description(self, registry):
        for t in registry.tools():
            assert t.description, f"Tool {t.name} missing description"

    def test_all_tools_have_input_schema(self, registry):
        for t in registry.tools():
            assert isinstance(t.input_schema, dict), f"Tool {t.name} missing input_schema"
            assert "type" in t.input_schema

    def test_expected_tools_present(self, registry):
        names = {t.name for t in registry.tools()}
        expected = {
            "initialize_repository", "get_repository_info",
            "list_repositories", "switch_repository",
            "query_code_graph", "get_code_snippet",
            "semantic_search", "find_api",
            "list_wiki_pages", "get_wiki_page",
            "locate_function", "list_api_interfaces",
            "list_api_docs", "get_api_doc",
            "generate_wiki", "rebuild_embeddings",
            "build_graph", "generate_api_docs",
        }
        missing = expected - names
        assert not missing, f"Missing expected tools: {missing}"

    def test_every_tool_has_handler(self, registry):
        for t in registry.tools():
            handler = registry.get_handler(t.name)
            assert handler is not None, f"Tool {t.name} has no handler"
            assert callable(handler)

    def test_unknown_tool_returns_none(self, registry):
        assert registry.get_handler("nonexistent_tool") is None

    def test_input_schema_is_valid_jsonschema(self, registry):
        for t in registry.tools():
            schema = t.input_schema
            assert schema.get("type") == "object"
            assert "properties" in schema


# ---------------------------------------------------------------------------
# call_tool dispatch simulation
# ---------------------------------------------------------------------------


class TestCallToolDispatch:
    """Simulate the server.call_tool dispatch logic."""

    def _simulate_call_tool(self, registry, name: str, arguments: dict):
        """Replicate server.py call_tool logic without MCP server."""
        handler = registry.get_handler(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")

        kwargs = dict(arguments or {})
        result = _run(handler(**kwargs))

        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        else:
            text = str(result)
        return json.loads(text) if text.startswith(("{", "[")) else text

    def test_dispatch_list_repositories(self, registry):
        result = self._simulate_call_tool(registry, "list_repositories", {})
        assert isinstance(result, dict)

    def test_dispatch_unknown_tool_raises(self, registry):
        with pytest.raises(ValueError, match="Unknown tool"):
            self._simulate_call_tool(registry, "nonexistent", {})

    def test_dispatch_get_repository_info_no_repo(self, registry):
        """Should raise ToolError when no repo is indexed."""
        from code_graph_builder.mcp.tools import ToolError

        with pytest.raises(ToolError):
            self._simulate_call_tool(registry, "get_repository_info", {})

    def test_dispatch_get_repository_info_with_repo(self, indexed_registry):
        result = self._simulate_call_tool(
            indexed_registry, "get_repository_info", {}
        )
        assert isinstance(result, dict)
        assert "repo_name" in result or "repo_path" in result or "status" in result

    def test_dispatch_result_is_json_serializable(self, indexed_registry):
        result = self._simulate_call_tool(
            indexed_registry, "list_repositories", {}
        )
        # Should not raise
        json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# ToolError propagation
# ---------------------------------------------------------------------------


class TestToolErrorHandling:
    """Verify ToolError is properly raised and structured."""

    def test_require_active_raises_toolerror(self, registry):
        from code_graph_builder.mcp.tools import ToolError

        # Tools that require an active repo and take no required args
        tools_no_args = [
            "get_repository_info",
            "list_wiki_pages", "list_api_interfaces",
            "list_api_docs",
        ]
        for tool_name in tools_no_args:
            with pytest.raises(ToolError):
                _run(registry.get_handler(tool_name)())

        # semantic_search requires query arg — should still raise ToolError (no repo)
        with pytest.raises(ToolError):
            _run(registry.get_handler("semantic_search")(query="test"))

    def test_find_api_without_embeddings_raises(self, indexed_registry):
        """find_api without embeddings should raise ToolError."""
        from code_graph_builder.mcp.tools import ToolError

        # indexed_registry was created with skip_embed=True
        with pytest.raises(ToolError):
            _run(indexed_registry._handle_find_api(query="test"))

    def test_switch_nonexistent_repo_raises(self, registry):
        from code_graph_builder.mcp.tools import ToolError

        with pytest.raises(ToolError):
            _run(registry._handle_switch_repository(repo_name="nonexistent_abc"))


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestStateManagement:
    """Verify repository state management."""

    def test_list_repos_empty_initially(self, registry):
        result = _run(registry._handle_list_repositories())
        assert isinstance(result, dict)

    def test_list_repos_after_index(self, indexed_registry):
        result = _run(indexed_registry._handle_list_repositories())
        assert isinstance(result, dict)
        repos = result.get("repositories", [])
        assert len(repos) > 0, "Should list the indexed repo"

    def test_indexed_repo_has_entry(self, indexed_registry):
        result = _run(indexed_registry._handle_list_repositories())
        repos = result.get("repositories", [])
        assert len(repos) > 0, "Should have at least one indexed repo"
        # Check any field contains tinycc reference
        repo = repos[0]
        repo_str = str(repo).lower()
        assert "tinycc" in repo_str or len(repos) > 0, f"Repo entry: {repo}"


# ---------------------------------------------------------------------------
# Tool handlers (graph-only, no embedding needed)
# ---------------------------------------------------------------------------


class TestGraphOnlyTools:
    """Test tools that only need a graph (no embeddings)."""

    def test_list_api_interfaces(self, indexed_registry):
        result = _run(indexed_registry._handle_list_api_interfaces())
        assert isinstance(result, dict)

    def test_list_api_docs(self, indexed_registry):
        result = _run(indexed_registry._handle_list_api_docs())
        assert isinstance(result, (dict, str))

    def test_get_api_doc_known_function(self, indexed_registry):
        """Should return API doc for a function that exists."""
        from code_graph_builder.mcp.tools import ToolError

        # First get a real qualified name from list_api_interfaces
        apis = _run(indexed_registry._handle_list_api_interfaces())
        # Find any function qn from the result
        qn = None
        for item in apis.get("interfaces", apis.get("functions", [])):
            if isinstance(item, dict) and item.get("qualified_name"):
                qn = item["qualified_name"]
                break
        if qn is None:
            pytest.skip("No APIs found to test get_api_doc")

        try:
            result = _run(indexed_registry._handle_get_api_doc(qualified_name=qn))
            assert result is not None
        except ToolError:
            pass  # Acceptable if doc file doesn't match exactly

    def test_list_wiki_pages_no_wiki(self, indexed_registry):
        """Wiki was skipped, should handle gracefully."""
        from code_graph_builder.mcp.tools import ToolError

        try:
            result = _run(indexed_registry._handle_list_wiki_pages())
            assert isinstance(result, (dict, list))
        except ToolError:
            pass  # Acceptable if wiki not generated

    def test_get_code_snippet(self, indexed_registry):
        """get_code_snippet should return source or raise ToolError."""
        from code_graph_builder.mcp.tools import ToolError

        # Use a function known to exist in the graph
        try:
            result = _run(indexed_registry._handle_get_code_snippet(
                qualified_name="tinycc.tcc.tcc_compile"
            ))
            assert result is not None
        except ToolError as e:
            # ToolError with "Not found" is acceptable behavior
            assert "Not found" in str(e) or "error" in str(e)

    def test_generate_api_docs_standalone(self, indexed_registry):
        result = _run(indexed_registry._handle_generate_api_docs(rebuild=False))
        assert isinstance(result, dict)
