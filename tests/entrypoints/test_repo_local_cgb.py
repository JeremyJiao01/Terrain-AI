"""Tests for repo-local .cgb/ artifact directory resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock


def _make_artifact_dir(path: Path, repo_path: str = "/fake/repo") -> None:
    """Create a minimal artifact dir with meta.json and graph.db."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "graph.db").write_bytes(b"fake")
    (path / "meta.json").write_text(
        json.dumps({"repo_path": repo_path, "repo_name": "repo", "steps": {"graph": True}}),
        encoding="utf-8",
    )


class TestResolveArtifactDir:
    """_resolve_artifact_dir prefers {repo_path}/.cgb/ over workspace artifact dir."""

    def test_prefers_local_cgb_when_exists(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()
        local_cgb = repo / ".cgb"
        _make_artifact_dir(local_cgb, repo_path=repo.as_posix())

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == local_cgb

    def test_falls_back_to_workspace_when_no_local_cgb(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_local_cgb_has_no_graph_db(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        local_cgb = repo / ".cgb"
        local_cgb.mkdir(parents=True)
        (local_cgb / "meta.json").write_text("{}", encoding="utf-8")

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_repo_path_missing_from_meta(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import _resolve_artifact_dir

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        ws_artifact.mkdir(parents=True)
        (ws_artifact / "graph.db").write_bytes(b"fake")
        (ws_artifact / "meta.json").write_text(
            json.dumps({"repo_name": "repo"}), encoding="utf-8"
        )

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_repo_path_does_not_exist(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import _resolve_artifact_dir

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path="/nonexistent/path")

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact


class TestMCPAutoLoadWithLocalCgb:
    """MCPToolsRegistry._try_auto_load() should prefer .cgb/ when available."""

    def test_auto_load_uses_local_cgb(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import MCPToolsRegistry

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        local_cgb = repo / ".cgb"
        _make_artifact_dir(local_cgb, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        with patch.object(MCPToolsRegistry, "_load_services") as mock_load:
            registry = MCPToolsRegistry(workspace=ws)
            mock_load.assert_called_once_with(local_cgb)

    def test_auto_load_falls_back_to_workspace(self, tmp_path: Path):
        from code_graph_builder.entrypoints.mcp.tools import MCPToolsRegistry

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        with patch.object(MCPToolsRegistry, "_load_services") as mock_load:
            registry = MCPToolsRegistry(workspace=ws)
            mock_load.assert_called_once_with(ws_artifact)


class TestCLILoadReposWithLocalCgb:
    """CLI _load_repos() should resolve .cgb/ for repos that have it."""

    def test_load_repos_resolves_local_cgb(self, tmp_path: Path):
        from code_graph_builder.entrypoints.cli.cli import _load_repos

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        local_cgb = repo / ".cgb"
        _make_artifact_dir(local_cgb, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        repos = _load_repos(ws)
        assert len(repos) == 1
        assert repos[0]["artifact_dir"] == local_cgb

    def test_load_repos_keeps_workspace_when_no_local_cgb(self, tmp_path: Path):
        from code_graph_builder.entrypoints.cli.cli import _load_repos

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        repos = _load_repos(ws)
        assert len(repos) == 1
        assert repos[0]["artifact_dir"] == ws_artifact
