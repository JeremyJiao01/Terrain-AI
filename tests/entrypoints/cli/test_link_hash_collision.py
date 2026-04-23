"""JER-101 — ``terrain link`` must refuse a same-hash collision where the
existing artifact dir already records a *different* normalized repo_path.

Before PR-2, the ``target_dir == artifact_dir`` branch in ``cmd_link`` blindly
overwrote ``repo_path`` in meta.json, silently erasing the previous link.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from terrain.entrypoints.cli import cli as cli_mod
from terrain.entrypoints.mcp.pipeline import artifact_dir_for
from terrain.foundation.utils.paths import normalize_repo_path


def _seed_artifact(ws: Path, repo: Path, stored_repo_path: str) -> Path:
    """Create an artifact dir whose hash matches *repo*, but whose meta
    records ``stored_repo_path`` (simulating a prior link to a different
    repo that happened to collide on the 8-char md5 prefix)."""
    artifact_dir = artifact_dir_for(ws, repo)
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "graph.db").write_bytes(b"")
    (artifact_dir / "meta.json").write_text(
        json.dumps({
            "schema_version": 2,
            "repo_path": stored_repo_path,
            "repo_name": Path(stored_repo_path).name,
            "indexed_at": "2026-04-20T00:00:00",
            "steps": {"graph": True, "api_docs": False,
                      "embeddings": False, "wiki": False},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact_dir


def test_same_hash_different_repo_path_is_refused(tmp_path: Path,
                                                   monkeypatch, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()

    repo = tmp_path / "project"
    repo.mkdir()

    # Seed the artifact so it records a different logical repo.
    colliding_path = "/somewhere/else/project_v1"
    artifact_dir = _seed_artifact(ws, repo, colliding_path)
    meta_before = (artifact_dir / "meta.json").read_bytes()

    monkeypatch.setenv("TERRAIN_WORKSPACE", str(ws))

    args = argparse.Namespace(repo_path=str(repo), db=None)
    # Bypass the interactive menu: only one candidate.
    rc = cli_mod.cmd_link(args)

    assert rc != 0
    # Refusal must not touch the existing meta.json.
    assert (artifact_dir / "meta.json").read_bytes() == meta_before

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "refus" in combined.lower() or "differ" in combined.lower() \
        or "conflict" in combined.lower() or "existing" in combined.lower()


def test_same_hash_matching_repo_path_still_works(tmp_path: Path,
                                                   monkeypatch, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    repo = tmp_path / "project"
    repo.mkdir()

    # Seed with the same normalized repo_path — re-link is benign.
    artifact_dir = _seed_artifact(ws, repo, normalize_repo_path(repo))

    monkeypatch.setenv("TERRAIN_WORKSPACE", str(ws))
    args = argparse.Namespace(repo_path=str(repo), db=None)
    rc = cli_mod.cmd_link(args)
    assert rc == 0

    meta = json.loads((artifact_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["repo_path"] == normalize_repo_path(repo)
