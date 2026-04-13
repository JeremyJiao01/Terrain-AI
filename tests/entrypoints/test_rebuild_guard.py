# tests/entrypoints/test_rebuild_guard.py
from __future__ import annotations
import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_args(step: str | None = None, backend: str = "kuzu", wiki: bool = False, no_llm: bool = False, mode: str = "comprehensive") -> argparse.Namespace:
    return argparse.Namespace(step=step, backend=backend, wiki=wiki, no_llm=no_llm, mode=mode)


def _setup_ws(tmp_path: Path, create_graph_db: bool = False) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    artifact_dir = ws / "myrepo"
    artifact_dir.mkdir()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    meta = {"repo_path": str(repo_path)}
    (artifact_dir / "meta.json").write_text(json.dumps(meta))
    (ws / "active.txt").write_text("myrepo")
    if create_graph_db:
        (artifact_dir / "graph.db").touch()
    return ws


class TestRebuildEmbedGuard:
    """cmd_rebuild --step embed should fail fast when graph.db is absent."""

    def test_embed_step_errors_when_no_graph_db(self, tmp_path: Path, capsys):
        from terrain.entrypoints.cli.cli import cmd_rebuild

        ws = _setup_ws(tmp_path, create_graph_db=False)

        with patch("terrain.entrypoints.cli.cli._get_workspace_root", return_value=ws):
            rc = cmd_rebuild(_make_args(step="embed"))

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "graph" in captured.out.lower()


class TestRebuildApiGuard:
    """cmd_rebuild --step api should fail fast when graph.db is absent."""

    def test_api_step_errors_when_no_graph_db(self, tmp_path: Path, capsys):
        from terrain.entrypoints.cli.cli import cmd_rebuild

        ws = _setup_ws(tmp_path, create_graph_db=False)

        with patch("terrain.entrypoints.cli.cli._get_workspace_root", return_value=ws):
            rc = cmd_rebuild(_make_args(step="api"))

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "graph" in captured.out.lower()
