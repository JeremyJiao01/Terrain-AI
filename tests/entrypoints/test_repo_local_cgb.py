"""Tests for repo-local .terrain/ artifact directory resolution."""
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
    """_resolve_artifact_dir prefers {repo_path}/.terrain/ over workspace artifact dir."""

    def test_prefers_local_terrain_when_exists(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()
        local_terrain = repo / ".terrain"
        _make_artifact_dir(local_terrain, repo_path=repo.as_posix())

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == local_terrain

    def test_falls_back_to_workspace_when_no_local_terrain(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_local_terrain_has_no_graph_db(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import _resolve_artifact_dir

        repo = tmp_path / "myrepo"
        local_terrain = repo / ".terrain"
        local_terrain.mkdir(parents=True)
        (local_terrain / "meta.json").write_text("{}", encoding="utf-8")

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_repo_path_missing_from_meta(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import _resolve_artifact_dir

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        ws_artifact.mkdir(parents=True)
        (ws_artifact / "graph.db").write_bytes(b"fake")
        (ws_artifact / "meta.json").write_text(
            json.dumps({"repo_name": "repo"}), encoding="utf-8"
        )

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact

    def test_falls_back_when_repo_path_does_not_exist(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import _resolve_artifact_dir

        ws_artifact = tmp_path / "workspace" / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path="/nonexistent/path")

        result = _resolve_artifact_dir(ws_artifact)
        assert result == ws_artifact


class TestMCPAutoLoadWithLocalTerrain:
    """MCPToolsRegistry._try_auto_load() should prefer .terrain/ when available."""

    def test_auto_load_uses_local_terrain(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        local_terrain = repo / ".terrain"
        _make_artifact_dir(local_terrain, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        with patch.object(MCPToolsRegistry, "_load_services") as mock_load:
            registry = MCPToolsRegistry(workspace=ws)
            mock_load.assert_called_once_with(local_terrain)

    def test_auto_load_falls_back_to_workspace(self, tmp_path: Path):
        from terrain.entrypoints.mcp.tools import MCPToolsRegistry

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


class TestCLILoadReposWithLocalTerrain:
    """CLI _load_repos() should resolve .terrain/ for repos that have it."""

    def test_load_repos_resolves_local_terrain(self, tmp_path: Path):
        from terrain.entrypoints.cli.cli import _load_repos

        ws = tmp_path / "workspace"
        ws.mkdir()

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws_artifact = ws / "myrepo_abc123"
        _make_artifact_dir(ws_artifact, repo_path=repo.as_posix())

        local_terrain = repo / ".terrain"
        _make_artifact_dir(local_terrain, repo_path=repo.as_posix())

        (ws / "active.txt").write_text("myrepo_abc123", encoding="utf-8")

        repos = _load_repos(ws)
        assert len(repos) == 1
        assert repos[0]["artifact_dir"] == local_terrain

    def test_load_repos_keeps_workspace_when_no_local_terrain(self, tmp_path: Path):
        from terrain.entrypoints.cli.cli import _load_repos

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


class TestIndexOutputDestination:
    """terrain index should support --output local/workspace flags."""

    def test_output_local_sets_artifact_dir_to_terrain(self, tmp_path: Path):
        from terrain.entrypoints.cli.cli import _resolve_index_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = tmp_path / "workspace"
        ws.mkdir()

        result = _resolve_index_artifact_dir(repo, ws, output="local")
        assert result == repo / ".terrain"

    def test_output_workspace_sets_artifact_dir_to_workspace(self, tmp_path: Path):
        from terrain.entrypoints.cli.cli import _resolve_index_artifact_dir
        from terrain.entrypoints.mcp.pipeline import artifact_dir_for

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = tmp_path / "workspace"
        ws.mkdir()

        result = _resolve_index_artifact_dir(repo, ws, output="workspace")
        expected = artifact_dir_for(ws, repo)
        assert result == expected

    def test_output_none_defaults_to_local_non_interactive(self, tmp_path: Path):
        from terrain.entrypoints.cli.cli import _resolve_index_artifact_dir

        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = tmp_path / "workspace"
        ws.mkdir()

        result = _resolve_index_artifact_dir(repo, ws, output=None, interactive=False)
        assert result == repo / ".terrain"
