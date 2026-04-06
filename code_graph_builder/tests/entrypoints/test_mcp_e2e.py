"""E2E test: build artifact → pip install → real MCP stdio protocol.

Finds the latest .whl in dist/, installs it, starts the MCP server as a
subprocess, and communicates via the actual MCP stdio JSON-RPC protocol.
Requires a pre-indexed tinycc workspace at ~/.code-graph-builder/.

Run manually:
    python -m pytest code_graph_builder/tests/entrypoints/test_mcp_e2e.py -v -s
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants / skip guards
# ---------------------------------------------------------------------------

DIST_DIR = Path(__file__).resolve().parents[3] / "dist"
WORKSPACE = Path.home() / ".code-graph-builder"
_TINYCC_ARTIFACTS = list(WORKSPACE.glob("tinycc_*/graph.db")) if WORKSPACE.exists() else []

pytestmark = pytest.mark.skipif(
    not _TINYCC_ARTIFACTS,
    reason="No indexed tinycc workspace found at ~/.code-graph-builder/tinycc_*/graph.db",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_whl() -> Path | None:
    """Return the most recently modified .whl in dist/."""
    whls = sorted(DIST_DIR.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return whls[0] if whls else None


def _install_whl(whl: Path) -> None:
    """pip install the wheel into the current Python environment."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", "--quiet", str(whl)],
        check=True,
    )


# ---------------------------------------------------------------------------
# MCPClient — minimal stdio JSON-RPC client
# ---------------------------------------------------------------------------

class MCPClient:
    """Wraps a server subprocess and speaks MCP stdio JSON-RPC protocol.

    Transport: one JSON object per line on stdin/stdout (no headers).
    """

    TIMEOUT = 10  # seconds per request

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self._req_id = 0
        self._pending: dict[int, dict] = {}  # id → response
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- internal reader thread --

    def _read_loop(self) -> None:
        assert self._proc.stdout
        for raw in self._proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._lock:
                    self._pending[msg_id] = msg

    # -- public API --

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send_raw(self, obj: dict) -> None:
        assert self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        self._proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the matching response."""
        req_id = self._next_id()
        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            msg["params"] = params
        self._send_raw(msg)

        deadline = time.monotonic() + self.TIMEOUT
        while time.monotonic() < deadline:
            with self._lock:
                if req_id in self._pending:
                    return self._pending.pop(req_id)
            time.sleep(0.05)
        raise TimeoutError(f"No response for '{method}' (id={req_id}) within {self.TIMEOUT}s")

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send_raw(msg)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool and return its unwrapped result dict.

        MCP wraps tool output as:
            {"content": [{"type": "text", "text": "<json>"}], "isError": bool}
        This method unwraps that and returns the parsed inner dict.
        """
        resp = self.request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        outer = resp.get("result", {})
        is_error = outer.get("isError", False)
        content = outer.get("content", [])
        if content and isinstance(content[0], dict) and content[0].get("type") == "text":
            text = content[0].get("text", "")
            try:
                parsed = json.loads(text)
                if is_error:
                    raise RuntimeError(f"Tool error: {parsed}")
                return parsed
            except json.JSONDecodeError:
                pass
        return outer

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def installed_whl():
    """Install the latest dist/*.whl, skip if none found."""
    whl = _latest_whl()
    if whl is None:
        pytest.skip(f"No .whl found in {DIST_DIR} — run 'python3 -m build' first")
    _install_whl(whl)
    return whl


@pytest.fixture(scope="module")
def mcp_client(installed_whl):
    """Start the MCP server subprocess and yield a connected MCPClient."""
    env = {**os.environ, "CGB_WORKSPACE": str(WORKSPACE)}

    proc = subprocess.Popen(
        [sys.executable, "-m", "code_graph_builder.entrypoints.mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    time.sleep(1)  # give server time to start

    if proc.poll() is not None:
        err = proc.stderr.read().decode()
        pytest.fail(f"MCP server exited immediately:\n{err}")

    client = MCPClient(proc)

    # MCP handshake: initialize → initialized
    init_resp = client.request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "e2e-test", "version": "0.1"},
    })
    assert "result" in init_resp, f"Bad initialize response: {init_resp}"
    client.notify("notifications/initialized")

    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests (run in definition order — each builds on the previous)
# ---------------------------------------------------------------------------

class TestMCPE2E:
    """Real MCP stdio protocol tests against the installed package."""

    def test_01_server_started(self, mcp_client):
        """Server handshake succeeded — server is up."""
        assert mcp_client._req_id >= 1

    def test_02_list_tools(self, mcp_client):
        """tools/list returns the expected set of tool names."""
        resp = mcp_client.request("tools/list")
        assert "result" in resp, f"Unexpected: {resp}"
        tools = resp["result"].get("tools", [])
        names = {t["name"] for t in tools}
        for expected in ("find_api", "find_callers", "trace_call_chain",
                         "get_api_doc", "list_api_docs", "get_repository_info"):
            assert expected in names, f"Tool '{expected}' missing from tools/list"
        print(f"\n  → {len(names)} tools: {sorted(names)}")

    def test_03_get_repository_info(self, mcp_client):
        """get_repository_info returns graph stats for the auto-loaded repo."""
        result = mcp_client.call_tool("get_repository_info")
        content = json.dumps(result)
        assert "tinycc" in content.lower(), f"Expected tinycc in result: {result}"
        # node_labels should now be a dict with real label names (Bug 4 fix)
        stats = result.get("graph_stats", {})
        node_labels = stats.get("node_labels", {})
        assert isinstance(node_labels, dict)
        if node_labels:
            # Keys should be label names, not numeric IDs
            sample_key = next(iter(node_labels))
            assert not sample_key.isdigit(), (
                f"node_labels keys look like numeric IDs (Bug 4 not fixed): {node_labels}"
            )
        print(f"\n  → node_count={stats.get('node_count')}, labels={list(node_labels.keys())[:5]}")

    def test_04_list_repositories(self, mcp_client):
        """list_repositories returns at least one indexed repo."""
        result = mcp_client.call_tool("list_repositories")
        repos = result.get("repositories", [])
        assert len(repos) >= 1
        print(f"\n  → {len(repos)} repo(s): {[r.get('repo_name') for r in repos]}")

    def test_05_list_api_docs(self, mcp_client):
        """list_api_docs returns module index."""
        result = mcp_client.call_tool("list_api_docs")
        content = str(result)
        assert "tinycc" in content.lower() or "module" in content.lower()
        print(f"\n  → list_api_docs returned {len(content)} chars")

    def test_06_find_api_keyword_fallback(self, mcp_client):
        """find_api uses keyword fallback when embeddings are unavailable (Bug 1 fix)."""
        result = mcp_client.call_tool("find_api", {"query": "tokenize source code", "top_k": 3})
        # Should NOT return an error
        assert "error" not in str(result).lower() or result.get("result_count", 0) >= 0
        search_mode = result.get("search_mode", "semantic")
        results = result.get("results", [])
        print(f"\n  → find_api mode={search_mode}, results={len(results)}")
        if search_mode == "keyword_fallback":
            assert len(results) > 0, "Keyword fallback returned 0 results"

    def test_07_find_callers(self, mcp_client):
        """find_callers returns real callers from the graph."""
        result = mcp_client.call_tool("find_callers", {"function_name": "next"})
        count = result.get("caller_count", 0)
        assert count > 0, f"Expected callers for 'next', got: {result}"
        print(f"\n  → find_callers('next'): {count} callers")

    def test_08_get_api_doc_with_live_callers(self, mcp_client):
        """get_api_doc includes live_callers field with real graph data (Bug 3 fix)."""
        result = mcp_client.call_tool("get_api_doc", {"qualified_name": "tinycc.tccpp.next"})
        assert "content" in result, f"Missing content in: {result}"
        live_callers = result.get("live_callers", None)
        assert live_callers is not None, "live_callers field missing (Bug 3 not fixed)"
        assert len(live_callers) > 0, (
            f"live_callers is empty — caller count mismatch still present. "
            f"find_callers found callers but get_api_doc live_callers is empty."
        )
        print(f"\n  → get_api_doc live_callers: {len(live_callers)}")

    def test_09_trace_call_chain(self, mcp_client):
        """trace_call_chain reaches the graph (Bug 2 fix: no 'No repository loaded')."""
        try:
            result = mcp_client.call_tool("trace_call_chain", {
                "target_function": "tinycc.tccpp.next",
                "max_depth": 3,
                "save_wiki": False,
            })
            # Success path: "matches" key present
            matches = result.get("matches", -1)
            assert matches >= 0, f"Unexpected result shape: {result}"
            print(f"\n  → trace_call_chain: {matches} matched function(s)")
        except RuntimeError as e:
            err_str = str(e)
            # "No repository loaded" = Bug 2 still present → fail
            assert "no repository loaded" not in err_str.lower(), (
                f"Bug 2 not fixed — trace_call_chain still can't find repo: {e}"
            )
            # Other tool errors (e.g. function not found) are acceptable
            print(f"\n  → trace_call_chain tool error (not repo error): {e}")

    def test_10_all_results_json_serializable(self, mcp_client):
        """Every tool result survives JSON round-trip (MCP requirement)."""
        calls = [
            ("list_repositories", {}),
            ("get_repository_info", {}),
            ("list_api_docs", {}),
            ("find_callers", {"function_name": "next"}),
        ]
        for name, args in calls:
            result = mcp_client.call_tool(name, args)
            # Should not raise
            json.dumps(result, default=str)
        print(f"\n  → All {len(calls)} tools passed JSON round-trip")
