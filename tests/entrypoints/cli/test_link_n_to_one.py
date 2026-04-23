"""JER-101 — N:1 linked_repos semantics for ``terrain link``.

Before PR-2, linking a new repo to an existing artifact only mutated the
**target** (new) artifact's ``meta.json``; the authoritative ("source") DB
was oblivious to who was using it, and the data model (``repo_path`` as a
single string) couldn't express N:1.

Schema v2 adds:
  * ``source_artifact``  — on every *linked-target* meta, the name of the
    authoritative artifact dir.
  * ``linked_repos``      — only on the *source* (authoritative) meta. A
    list of ``{repo_path, repo_name, artifact_dir, linked_at}`` entries,
    keyed by ``artifact_dir``, idempotent under re-link.
"""
from __future__ import annotations

import json
from pathlib import Path

from terrain.foundation.utils.link_ops import register_link
from terrain.foundation.utils.paths import normalize_repo_path


def _write_source_meta(source_dir: Path, repo: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "meta.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "repo_path": normalize_repo_path(repo),
                "repo_name": repo.name,
                "indexed_at": "2026-04-23T00:00:00",
                "steps": {"graph": False, "api_docs": False,
                          "embeddings": False, "wiki": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_register_link_upserts_single_entry(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    source_repo = tmp_path / "project"
    source_repo.mkdir()
    source_dir = ws / "project_aaaa1111"
    _write_source_meta(source_dir, source_repo)

    repo_a = tmp_path / "project_clone_a"
    repo_a.mkdir()
    target_a = ws / "project_bbbb2222"
    target_a.mkdir()

    register_link(ws, source_dir=source_dir, target_dir=target_a,
                  repo_path=repo_a)

    # Target meta points back at source
    target_meta = json.loads((target_a / "meta.json").read_text(encoding="utf-8"))
    assert target_meta["schema_version"] == 2
    assert target_meta["source_artifact"] == source_dir.name
    assert target_meta["repo_path"] == normalize_repo_path(repo_a)
    assert target_meta["repo_name"] == "project_clone_a"
    # Target must NOT own linked_repos — only the source does.
    assert "linked_repos" not in target_meta

    # Source meta gained one linked_repos entry.
    source_meta = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    assert source_meta["schema_version"] == 2
    linked = source_meta["linked_repos"]
    assert len(linked) == 1
    assert linked[0]["artifact_dir"] == target_a.name
    assert linked[0]["repo_path"] == normalize_repo_path(repo_a)
    assert linked[0]["repo_name"] == "project_clone_a"
    assert "linked_at" in linked[0]


def test_register_link_accumulates_multiple_targets(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    source_repo = tmp_path / "project"
    source_repo.mkdir()
    source_dir = ws / "project_aaaa1111"
    _write_source_meta(source_dir, source_repo)

    repo_a = tmp_path / "repo_A"
    repo_a.mkdir()
    repo_b = tmp_path / "repo_B"
    repo_b.mkdir()

    target_a = ws / "project_bbbb2222"
    target_a.mkdir()
    target_b = ws / "project_cccc3333"
    target_b.mkdir()

    register_link(ws, source_dir=source_dir, target_dir=target_a,
                  repo_path=repo_a)
    register_link(ws, source_dir=source_dir, target_dir=target_b,
                  repo_path=repo_b)

    source_meta = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    linked = source_meta["linked_repos"]
    assert len(linked) == 2
    names = {e["artifact_dir"] for e in linked}
    assert names == {target_a.name, target_b.name}


def test_register_link_is_idempotent(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    source_repo = tmp_path / "project"
    source_repo.mkdir()
    source_dir = ws / "project_aaaa1111"
    _write_source_meta(source_dir, source_repo)

    repo_a = tmp_path / "repo_A"
    repo_a.mkdir()
    target_a = ws / "project_bbbb2222"
    target_a.mkdir()

    register_link(ws, source_dir=source_dir, target_dir=target_a,
                  repo_path=repo_a)
    first = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    first_linked_at = first["linked_repos"][0]["linked_at"]

    # Re-run with the same inputs — expect no duplicate and preserved linked_at
    register_link(ws, source_dir=source_dir, target_dir=target_a,
                  repo_path=repo_a)

    second = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    linked = second["linked_repos"]
    assert len(linked) == 1
    assert linked[0]["artifact_dir"] == target_a.name
    assert linked[0]["linked_at"] == first_linked_at
