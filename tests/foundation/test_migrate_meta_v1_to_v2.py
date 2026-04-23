"""JER-101 — lazy migration of meta.json from schema v1 to v2.

A v1 meta.json has no ``schema_version`` field. It may additionally carry
legacy link markers:

  * ``linked_from`` / ``linked_to`` — present on the linked-target side,
    pointing at the source artifact directory.

The migrator must be idempotent and run lazily (first CLI list/status or
MCP list_repositories read). Target dirs gain ``source_artifact``; source
dirs gain ``linked_repos`` rebuilt by scanning siblings whose ``linked_from``
or ``source_artifact`` points back here.
"""
from __future__ import annotations

import json
from pathlib import Path

from terrain.entrypoints.link_ops import migrate_meta_to_v2
from terrain.foundation.utils.paths import normalize_repo_path


def _write_meta(d: Path, meta: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def test_migrate_v1_target_gains_source_artifact(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    source_dir = ws / "project_aaaa1111"
    source_dir.mkdir()
    # Source meta (v1 — no schema_version, no linked_repos).
    _write_meta(source_dir, {
        "repo_path": "/tmp/project",
        "repo_name": "project",
        "indexed_at": "2026-04-20T00:00:00",
    })

    target_dir = ws / "clone_bbbb2222"
    _write_meta(target_dir, {
        "repo_path": "/tmp/clone",
        "repo_name": "clone",
        "linked_from": str(source_dir),  # v1 marker
        "linked_at": "2026-04-21T00:00:00",
    })

    migrate_meta_to_v2(target_dir, ws)

    meta = json.loads((target_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 2
    assert meta["source_artifact"] == source_dir.name
    # Preserved.
    assert meta["repo_path"] == "/tmp/clone"
    assert meta["linked_from"] == str(source_dir)


def test_migrate_v1_source_rebuilds_linked_repos(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    source_dir = ws / "project_aaaa1111"
    _write_meta(source_dir, {
        "repo_path": "/tmp/project",
        "repo_name": "project",
        "indexed_at": "2026-04-20T00:00:00",
    })

    clone_a = ws / "cloneA_bbbb2222"
    _write_meta(clone_a, {
        "repo_path": "/tmp/cloneA",
        "repo_name": "cloneA",
        "linked_from": str(source_dir),
        "linked_at": "2026-04-21T00:00:00",
    })
    clone_b = ws / "cloneB_cccc3333"
    _write_meta(clone_b, {
        "repo_path": "/tmp/cloneB",
        "repo_name": "cloneB",
        "linked_to": str(source_dir),  # alternate v1 marker
        "linked_at": "2026-04-22T00:00:00",
    })
    # Unrelated dir — must NOT appear in linked_repos.
    unrelated = ws / "other_dddd4444"
    _write_meta(unrelated, {
        "repo_path": "/tmp/other",
        "repo_name": "other",
    })

    migrate_meta_to_v2(source_dir, ws)

    meta = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 2
    linked = meta["linked_repos"]
    names = {e["artifact_dir"] for e in linked}
    assert names == {clone_a.name, clone_b.name}
    # Entries carry normalized repo_path + linked_at from the sibling meta.
    a_entry = next(e for e in linked if e["artifact_dir"] == clone_a.name)
    assert a_entry["repo_path"] == normalize_repo_path("/tmp/cloneA")
    assert a_entry["linked_at"] == "2026-04-21T00:00:00"


def test_migrate_is_idempotent(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ws / "project_aaaa1111"
    _write_meta(d, {
        "schema_version": 2,
        "repo_path": "/tmp/project",
        "repo_name": "project",
        "linked_repos": [
            {"artifact_dir": "clone_bbbb2222", "repo_path": "/tmp/clone",
             "repo_name": "clone", "linked_at": "2026-04-21T00:00:00"},
        ],
    })
    before = (d / "meta.json").read_bytes()
    migrate_meta_to_v2(d, ws)
    after = (d / "meta.json").read_bytes()
    assert before == after


def test_migrate_handles_missing_or_corrupt_meta(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()

    missing = ws / "no_meta_here"
    missing.mkdir()
    # Must not raise.
    migrate_meta_to_v2(missing, ws)
    assert not (missing / "meta.json").exists()

    corrupt = ws / "corrupt"
    corrupt.mkdir()
    (corrupt / "meta.json").write_bytes(b"\x00\x01not-json")
    # Must not raise and must not overwrite corrupt bytes.
    before = (corrupt / "meta.json").read_bytes()
    migrate_meta_to_v2(corrupt, ws)
    assert (corrupt / "meta.json").read_bytes() == before
