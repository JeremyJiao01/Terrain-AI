"""Tests for get_merge_diff MCP tool.

Covers:
- Auto-discover last two merge commits (no args)
- Manual SHA specification
- No merge history → clear error
- Empty diff between merges → empty function list
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terrain.foundation.services.git_service import GitChangeDetector


# ---------------------------------------------------------------------------
# GitChangeDetector unit tests
# ---------------------------------------------------------------------------


class TestGetMergeCommits:
    """Tests for GitChangeDetector.get_merge_commits."""

    def _make_repo_with_branch_merges(self, tmp_path):
        """Helper: create a repo with merges on main and a feature branch."""
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

        # Initial commit on main
        (tmp_path / "main.txt").write_text("main")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

        # Feature branch 1 → merge into main
        subprocess.run(["git", "checkout", "-b", "feat1"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "feat1.txt").write_text("feat1")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat1 work"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", "feat1", "-m", "merge feat1"], cwd=tmp_path, check=True, capture_output=True)

        # Feature branch 2 → merge into main
        subprocess.run(["git", "checkout", "-b", "feat2"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "feat2.txt").write_text("feat2")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat2 work"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", "feat2", "-m", "merge feat2"], cwd=tmp_path, check=True, capture_output=True)

        return tmp_path

    def test_returns_merge_shas_in_order(self, tmp_path):
        """Returns the most-recent merge commits, newest first."""
        self._make_repo_with_branch_merges(tmp_path)

        detector = GitChangeDetector()
        merges = detector.get_merge_commits(tmp_path, limit=2)

        assert len(merges) == 2
        # All should be 40-char hex SHAs
        for sha in merges:
            assert len(sha) == 40
            assert all(c in "0123456789abcdef" for c in sha)

    def test_no_merge_history_returns_empty(self, tmp_path):
        """Returns empty list when no merge commits exist."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "only commit"], cwd=tmp_path, check=True, capture_output=True)

        detector = GitChangeDetector()
        merges = detector.get_merge_commits(tmp_path, limit=2)
        assert merges == []

    def test_non_git_dir_returns_empty(self, tmp_path):
        """Returns empty list for non-git directories."""
        detector = GitChangeDetector()
        merges = detector.get_merge_commits(tmp_path, limit=2)
        assert merges == []

    def test_branch_param_searches_specified_branch(self, tmp_path):
        """When branch is given, returns merges from that branch's history."""
        self._make_repo_with_branch_merges(tmp_path)

        # Create a new branch with NO merge commits (diverges before merges)
        subprocess.run(["git", "checkout", "-b", "no-merges", "HEAD~2"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "other.txt").write_text("other")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "no-merge work"], cwd=tmp_path, check=True, capture_output=True)

        detector = GitChangeDetector()

        # From no-merges branch (HEAD), no merges visible
        merges_head = detector.get_merge_commits(tmp_path, limit=2)
        assert merges_head == []

        # But specifying branch=main should still find merges
        merges_main = detector.get_merge_commits(tmp_path, limit=2, branch="main")
        assert len(merges_main) == 2

    def test_branch_param_none_defaults_to_head(self, tmp_path):
        """When branch is None, behaves the same as before (uses HEAD)."""
        self._make_repo_with_branch_merges(tmp_path)

        detector = GitChangeDetector()
        merges_default = detector.get_merge_commits(tmp_path, limit=2)
        merges_none = detector.get_merge_commits(tmp_path, limit=2, branch=None)
        assert merges_default == merges_none


class TestGetChangedFilesBetween:
    """Tests for GitChangeDetector.get_changed_files_between."""

    def _make_repo_with_two_merges(self, tmp_path):
        """Helper: create a repo with two merge commits, return (merge1_sha, merge2_sha)."""
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

        (tmp_path / "base.py").write_text("# base")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)

        # First merge
        subprocess.run(["git", "checkout", "-b", "feat1"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "alpha.py").write_text("def alpha(): pass")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add alpha"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", "feat1", "-m", "merge feat1"], cwd=tmp_path, check=True, capture_output=True)
        merge1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()

        # Second merge
        subprocess.run(["git", "checkout", "-b", "feat2"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "beta.py").write_text("def beta(): pass")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add beta"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", "feat2", "-m", "merge feat2"], cwd=tmp_path, check=True, capture_output=True)
        merge2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()

        return merge1, merge2

    def test_returns_changed_files(self, tmp_path):
        """Returns files changed between two commits."""
        merge1, merge2 = self._make_repo_with_two_merges(tmp_path)
        detector = GitChangeDetector()
        changed = detector.get_changed_files_between(tmp_path, merge1, merge2)
        assert changed is not None
        names = {f.name for f in changed}
        assert "beta.py" in names

    def test_invalid_sha_returns_none(self, tmp_path):
        """Returns None when a commit SHA is not in git history."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

        detector = GitChangeDetector()
        result = detector.get_changed_files_between(tmp_path, "deadbeef" * 5, "cafebabe" * 5)
        assert result is None


# ---------------------------------------------------------------------------
# _handle_get_merge_diff integration tests (mock-based)
# ---------------------------------------------------------------------------


class TestHandleGetMergeDiff:
    """Tests for MCPToolsRegistry._handle_get_merge_diff."""

    def _make_registry(self, tmp_path):
        """Create a minimal MCPToolsRegistry pointed at tmp_path."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry
        ws = tmp_path / "workspace"
        ws.mkdir()
        registry = MCPToolsRegistry(ws)
        # Manually set active state without requiring a real indexed repo
        registry._active_repo_path = tmp_path / "repo"
        registry._active_artifact_dir = tmp_path / "artifact"
        registry._db_path = tmp_path / "artifact" / "graph.db"
        return registry

    def test_registered_in_tools_list(self, tmp_path):
        """get_merge_diff must appear in the tools() list."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry
        ws = tmp_path / "workspace"
        ws.mkdir()
        registry = MCPToolsRegistry(ws)
        names = [t.name for t in registry.tools()]
        assert "get_merge_diff" in names

    def test_registered_in_get_handler(self, tmp_path):
        """get_merge_diff must be registered in get_handler()."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry
        ws = tmp_path / "workspace"
        ws.mkdir()
        registry = MCPToolsRegistry(ws)
        assert registry.get_handler("get_merge_diff") is not None

    def test_no_active_repo_raises_tool_error(self, tmp_path):
        """Raises ToolError when no repo is active."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry, ToolError
        ws = tmp_path / "workspace"
        ws.mkdir()
        registry = MCPToolsRegistry(ws)

        with pytest.raises(ToolError, match="No active repository|No repository"):
            asyncio.run(registry._handle_get_merge_diff())

    def test_auto_discover_no_merge_history_raises(self, tmp_path):
        """Raises ToolError when repo has fewer than 2 merge commits."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry, ToolError

        registry = self._make_registry(tmp_path)

        with patch(
            "terrain.entrypoints.mcp.tools._GCD.get_merge_commits",
            return_value=[],
        ):
            with pytest.raises(ToolError, match="[Ll]ess than 2 merge"):
                asyncio.run(registry._handle_get_merge_diff())

    def test_manual_sha_invalid_returns_error(self, tmp_path):
        """Raises ToolError when specified SHA is not in git history."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry, ToolError

        registry = self._make_registry(tmp_path)

        with patch(
            "terrain.entrypoints.mcp.tools._GCD.get_changed_files_between",
            return_value=None,
        ):
            with pytest.raises(ToolError, match="[Nn]ot in git history|not found|[Ii]nvalid"):
                asyncio.run(
                    registry._handle_get_merge_diff(
                        from_merge="aaaa" * 10, to_merge="bbbb" * 10
                    )
                )

    def test_auto_discover_returns_function_list(self, tmp_path):
        """Returns well-formed response when diff is found and kuzu has data."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        registry = self._make_registry(tmp_path)
        repo_path = registry._active_repo_path

        merge1 = "a" * 40
        merge2 = "b" * 40
        fake_changed = [repo_path / "src" / "foo.py"]
        fake_rows = [
            {
                "qn": "src.foo.bar",
                "fname": "bar",
                "fpath": "src/foo.py",
                "start": 10,
            }
        ]

        with (
            patch("terrain.entrypoints.mcp.tools._GCD.get_merge_commits", return_value=[merge2, merge1]),
            patch("terrain.entrypoints.mcp.tools._GCD.get_changed_files_between", return_value=fake_changed),
            patch.object(registry, "_temporary_ingestor") as mock_ctx,
        ):
            mock_ingestor = MagicMock()
            mock_ingestor.query.return_value = fake_rows
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ingestor)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = asyncio.run(registry._handle_get_merge_diff())

        assert result["from_merge"] == merge1
        assert result["to_merge"] == merge2
        assert result["changed_files"] == 1
        assert isinstance(result["functions"], list)

    def test_empty_diff_returns_empty_functions(self, tmp_path):
        """Returns empty function list when no files changed between merges."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        registry = self._make_registry(tmp_path)

        merge1 = "c" * 40
        merge2 = "d" * 40

        with (
            patch("terrain.entrypoints.mcp.tools._GCD.get_merge_commits", return_value=[merge2, merge1]),
            patch("terrain.entrypoints.mcp.tools._GCD.get_changed_files_between", return_value=[]),
            patch.object(registry, "_temporary_ingestor") as mock_ctx,
        ):
            mock_ingestor = MagicMock()
            mock_ingestor.query.return_value = []
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ingestor)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = asyncio.run(registry._handle_get_merge_diff())

        assert result["changed_files"] == 0
        assert result["functions"] == []

    def test_branch_param_passed_to_detector(self, tmp_path):
        """Branch parameter is forwarded to get_merge_commits."""
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        registry = self._make_registry(tmp_path)
        repo_path = registry._active_repo_path

        merge1 = "e" * 40
        merge2 = "f" * 40

        with (
            patch("terrain.entrypoints.mcp.tools._GCD.get_merge_commits", return_value=[merge2, merge1]) as mock_gmc,
            patch("terrain.entrypoints.mcp.tools._GCD.get_changed_files_between", return_value=[]),
            patch.object(registry, "_temporary_ingestor") as mock_ctx,
        ):
            mock_ingestor = MagicMock()
            mock_ingestor.query.return_value = []
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_ingestor)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            asyncio.run(registry._handle_get_merge_diff(branch="origin/main"))

        # Verify branch was passed through to get_merge_commits
        mock_gmc.assert_called_once_with(repo_path, 2, "origin/main")
