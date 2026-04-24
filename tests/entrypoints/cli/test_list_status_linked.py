"""JER-102 — ``terrain list`` / ``terrain status`` rendering for linked repos.

Schema v2 (from JER-101) introduces two new meta.json fields:

* ``source_artifact`` on every *child* (linked-target) meta — reverse pointer
  to the authoritative artifact dir.
* ``linked_repos`` on the *source* (authoritative) meta — list of all repo
  mounts sharing this database.

Consumers (``_load_repos``, ``_get_repo_status_entries``) must now surface:

* a ``linked_source`` field on entries whose artifact is a child — so CLI
  ``list`` can render ``linked → <source_repo_name>`` and MCP
  ``list_repositories`` / ``get_repository_info`` can expose the pointer.
* a ``shared_count`` field on entries whose artifact is authoritative and
  has ``>=1`` linked_repos — so CLI ``status`` / ``list`` can display
  ``(shared by N repos)`` when ``N >= 2``.

Each linked child carries its own ``repo_path``, so the existing per-dir
loop already gives one status row per mount with its own
``GitChangeDetector`` result — no artificial expansion is needed.
"""
from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terrain.entrypoints.cli import cli as cli_mod
from terrain.foundation.utils.paths import normalize_repo_path


def _make_source_dir(ws: Path, artifact_name: str, repo: Path) -> Path:
    d = ws / artifact_name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
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
    return d


def _make_child_dir(ws: Path, artifact_name: str, repo: Path, source_dir: Path) -> Path:
    d = ws / artifact_name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_artifact": source_dir.name,
                "linked_from": str(source_dir),
                "repo_path": normalize_repo_path(repo),
                "repo_name": repo.name,
                "linked_at": "2026-04-23T00:00:00",
                "indexed_at": "2026-04-23T00:00:00",
                "steps": {"graph": False, "api_docs": False,
                          "embeddings": False, "wiki": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


def _upsert_linked_repos(source_dir: Path, entries: list[dict]) -> None:
    meta = json.loads((source_dir / "meta.json").read_text(encoding="utf-8"))
    meta["linked_repos"] = entries
    (source_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class TestLoadReposExposesLinkage:
    def test_child_entry_carries_linked_source(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()

        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        child_repo_a = tmp_path / "origin_clone_a"
        child_repo_a.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child_a = _make_child_dir(ws, "origin_bbbb2222", child_repo_a, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(child_repo_a),
             "repo_name": child_repo_a.name,
             "artifact_dir": child_a.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        repos = cli_mod._load_repos(ws)
        by_name = {r["name"]: r for r in repos}

        # Child row exposes linked_source (source artifact's repo_name).
        child_entry = by_name[child_repo_a.name]
        assert child_entry.get("linked_source") == src_repo.name

        # Source row itself does not set linked_source (it's the authority).
        source_entry = by_name[src_repo.name]
        assert source_entry.get("linked_source") in (None, "")

    def test_source_entry_has_shared_count(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        repo_a = tmp_path / "clone_a"
        repo_a.mkdir()
        repo_b = tmp_path / "clone_b"
        repo_b.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child_a = _make_child_dir(ws, "origin_bbbb2222", repo_a, source_dir)
        child_b = _make_child_dir(ws, "origin_cccc3333", repo_b, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(repo_a),
             "repo_name": repo_a.name, "artifact_dir": child_a.name,
             "linked_at": "2026-04-23T00:00:00"},
            {"repo_path": normalize_repo_path(repo_b),
             "repo_name": repo_b.name, "artifact_dir": child_b.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        repos = cli_mod._load_repos(ws)
        by_name = {r["name"]: r for r in repos}
        assert by_name[src_repo.name]["shared_count"] == 2
        # Children carry a link pointer, not a shared_count.
        assert by_name[repo_a.name].get("shared_count") in (None, 0)


class TestGetRepoStatusEntriesLinkage:
    def test_each_linked_mount_yields_its_own_entry(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        repo_a = tmp_path / "clone_a"
        repo_a.mkdir()
        repo_b = tmp_path / "clone_b"
        repo_b.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child_a = _make_child_dir(ws, "origin_bbbb2222", repo_a, source_dir)
        child_b = _make_child_dir(ws, "origin_cccc3333", repo_b, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(repo_a),
             "repo_name": repo_a.name, "artifact_dir": child_a.name,
             "linked_at": "2026-04-23T00:00:00"},
            {"repo_path": normalize_repo_path(repo_b),
             "repo_name": repo_b.name, "artifact_dir": child_b.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        entries = cli_mod._get_repo_status_entries(ws)
        # One entry per artifact dir — three total.
        names = {e["artifact_dir"] for e in entries}
        assert names == {source_dir.name, child_a.name, child_b.name}

        # Each child entry reports its own repo_path (independent git probe).
        child_a_entry = next(e for e in entries if e["artifact_dir"] == child_a.name)
        child_b_entry = next(e for e in entries if e["artifact_dir"] == child_b.name)
        assert child_a_entry["path"] == normalize_repo_path(repo_a)
        assert child_b_entry["path"] == normalize_repo_path(repo_b)

        # Children surface a linked_source pointer for downstream display.
        assert child_a_entry.get("linked_source") == src_repo.name
        assert child_b_entry.get("linked_source") == src_repo.name

        # Source surfaces shared_count so CLI can annotate ``(shared by N)``.
        source_entry = next(e for e in entries if e["artifact_dir"] == source_dir.name)
        assert source_entry.get("shared_count") == 2

    def test_standalone_repo_has_no_linkage_fields(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        repo = tmp_path / "solo"
        repo.mkdir()
        _make_source_dir(ws, "solo_aaaa1111", repo)

        entries = cli_mod._get_repo_status_entries(ws)
        assert len(entries) == 1
        e = entries[0]
        assert e.get("linked_source") in (None, "")
        assert e.get("shared_count") in (None, 0)


class TestCmdListRendersLinkedArrow:
    def test_list_marks_child_with_linked_arrow(self, tmp_path: Path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()

        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        child_repo = tmp_path / "origin_clone_a"
        child_repo.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child = _make_child_dir(ws, "origin_bbbb2222", child_repo, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(child_repo),
             "repo_name": child_repo.name, "artifact_dir": child.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        monkeypatch.setenv("TERRAIN_WORKSPACE", str(ws))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_mod.cmd_list(SimpleNamespace())
        assert rc == 0
        out = buf.getvalue()
        # The child row shows "linked → <source_repo_name>".
        assert f"linked → {src_repo.name}" in out

    def test_list_marks_authoritative_with_shared_count(self, tmp_path: Path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()

        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        repo_a = tmp_path / "clone_a"
        repo_a.mkdir()
        repo_b = tmp_path / "clone_b"
        repo_b.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child_a = _make_child_dir(ws, "origin_bbbb2222", repo_a, source_dir)
        child_b = _make_child_dir(ws, "origin_cccc3333", repo_b, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(repo_a),
             "repo_name": repo_a.name, "artifact_dir": child_a.name,
             "linked_at": "2026-04-23T00:00:00"},
            {"repo_path": normalize_repo_path(repo_b),
             "repo_name": repo_b.name, "artifact_dir": child_b.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        monkeypatch.setenv("TERRAIN_WORKSPACE", str(ws))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_mod.cmd_list(SimpleNamespace())
        assert rc == 0
        out = buf.getvalue()
        assert "shared by 2" in out


class TestCmdStatusJsonExposesLinkage:
    def test_status_json_reports_linked_source_and_shared_count(
        self, tmp_path: Path, monkeypatch
    ):
        ws = tmp_path / "ws"
        ws.mkdir()

        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        repo_a = tmp_path / "clone_a"
        repo_a.mkdir()
        repo_b = tmp_path / "clone_b"
        repo_b.mkdir()

        source_dir = _make_source_dir(ws, "origin_aaaa1111", src_repo)
        child_a = _make_child_dir(ws, "origin_bbbb2222", repo_a, source_dir)
        child_b = _make_child_dir(ws, "origin_cccc3333", repo_b, source_dir)
        _upsert_linked_repos(source_dir, [
            {"repo_path": normalize_repo_path(repo_a),
             "repo_name": repo_a.name, "artifact_dir": child_a.name,
             "linked_at": "2026-04-23T00:00:00"},
            {"repo_path": normalize_repo_path(repo_b),
             "repo_name": repo_b.name, "artifact_dir": child_b.name,
             "linked_at": "2026-04-23T00:00:00"},
        ])

        monkeypatch.setenv("TERRAIN_WORKSPACE", str(ws))
        buf = io.StringIO()
        ns = SimpleNamespace(json=True, debug=None)
        with redirect_stdout(buf):
            rc = cli_mod.cmd_status(ns)
        assert rc == 0
        payload = json.loads(buf.getvalue())
        by_dir = {e["artifact_dir"]: e for e in payload}

        assert by_dir[child_a.name]["linked_source"] == src_repo.name
        assert by_dir[child_b.name]["linked_source"] == src_repo.name
        assert by_dir[source_dir.name]["shared_count"] == 2


# ---------------------------------------------------------------------------
# JER-107 — linked children inherit source's SHA anchor for staleness.
#
# ``register_link`` does not write ``last_indexed_commit`` onto a child's
# meta.json; the authoritative source owns it. Without this inheritance,
# every linked child falls into the legacy ``count_commits_since`` path
# (naive ``--since timestamp``) and surfaces ``indexed_head=None``, which
# defeats the JER-102 "linked mount visibility" work.
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=path, check=True, capture_output=True,
    )


def _git_commit(path: Path, name: str, content: str, msg: str) -> str:
    (path / name).write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=path, check=True, capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write_meta(d: Path, meta: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class TestLinkedChildInheritsSourceShaAnchor:
    """Children must borrow the source's ``last_indexed_commit`` for staleness.

    Schema v2 (JER-101) + ``register_link`` (JER-102) never write
    ``last_indexed_commit`` onto the child's meta — only the source has it.
    ``_get_repo_status_entries`` must resolve the source row's anchor for
    every child, so CLI / MCP consumers see non-null ``indexed_head`` and
    a SHA-based ``commits_since`` instead of the naive timestamp fallback.
    """

    def _make_source_with_sha(
        self, ws: Path, artifact_name: str, repo: Path, sha: str
    ) -> Path:
        d = ws / artifact_name
        _write_meta(d, {
            "schema_version": 2,
            "repo_name": repo.name,
            "repo_path": normalize_repo_path(repo),
            "indexed_at": "2026-04-20T00:00:00+00:00",
            "last_indexed_commit": sha,
            "steps": {"graph": True, "api_docs": False,
                      "embeddings": False, "wiki": False},
        })
        return d

    def _make_child_no_sha(
        self, ws: Path, artifact_name: str, repo: Path, source_dir: Path
    ) -> Path:
        d = ws / artifact_name
        # Mirrors what ``register_link`` writes: no ``last_indexed_commit``.
        _write_meta(d, {
            "schema_version": 2,
            "source_artifact": source_dir.name,
            "linked_from": str(source_dir),
            "repo_path": normalize_repo_path(repo),
            "repo_name": repo.name,
            "linked_at": "2026-04-23T12:00:00+00:00",
            "indexed_at": "2026-04-23T12:00:00+00:00",
            "steps": {"graph": True, "api_docs": False,
                      "embeddings": False, "wiki": False},
        })
        return d

    def test_child_indexed_head_matches_source_short_sha_up_to_date(
        self, tmp_path: Path
    ):
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        _git_init(src_repo)
        head = _git_commit(src_repo, "a.txt", "a", "first")

        # Child mounts the *same* repo contents (typical for a link).
        child_repo = tmp_path / "clone_a"
        child_repo.mkdir()
        _git_init(child_repo)
        # Child's own HEAD is an unrelated commit — irrelevant for staleness
        # computation, which must be driven by the source's SHA.
        _git_commit(child_repo, "a.txt", "a", "first")

        src_dir = self._make_source_with_sha(
            ws, "origin_aaaa1111", src_repo, head
        )
        child_dir = self._make_child_no_sha(
            ws, "origin_bbbb2222", src_repo, src_dir
        )

        entries = cli_mod._get_repo_status_entries(ws)
        by_dir = {e["artifact_dir"]: e for e in entries}

        src_entry = by_dir[src_dir.name]
        child_entry = by_dir[child_dir.name]

        # Acceptance criterion 1: non-null indexed_head, same as source.
        assert src_entry["indexed_head"] == head[:7]
        assert child_entry["indexed_head"] == head[:7]
        # Source points at its own repo (same SHA → up-to-date).
        assert src_entry["commits_since"] == 0
        assert src_entry["status"] == "up-to-date"
        # Child's repo_path is the source repo, same SHA → up-to-date too.
        assert child_entry["commits_since"] == 0
        assert child_entry["status"] == "up-to-date"

    def test_child_commits_since_uses_sha_path_when_source_has_anchor(
        self, tmp_path: Path
    ):
        """Regression guard: child must go through count_commits_since_sha,
        not count_commits_since, whenever the source has last_indexed_commit.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        src_dir = self._make_source_with_sha(
            ws, "origin_aaaa1111", src_repo, "deadbeef" + "0" * 32
        )
        self._make_child_no_sha(ws, "origin_bbbb2222", src_repo, src_dir)

        with patch(
            "terrain.foundation.services.git_service.GitChangeDetector"
        ) as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since_sha.return_value = 0
            instance.count_commits_since.return_value = 0
            instance.get_current_head.return_value = None
            cli_mod._get_repo_status_entries(ws)

        # Both the source row AND the child row should take the SHA path.
        assert instance.count_commits_since_sha.call_count == 2
        # The legacy timestamp path must not run for either row.
        instance.count_commits_since.assert_not_called()

    def test_child_falls_back_to_legacy_path_when_source_missing_sha(
        self, tmp_path: Path
    ):
        """Edge: source meta present but has no last_indexed_commit.

        Use the source's ``indexed_at`` for the legacy ``--since`` path so
        pre-SHA-anchor workspaces still report *something*. The child's
        own ``indexed_at`` is kept for display only.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()

        src_dir = ws / "origin_aaaa1111"
        _write_meta(src_dir, {
            "schema_version": 2,
            "repo_name": src_repo.name,
            "repo_path": normalize_repo_path(src_repo),
            "indexed_at": "2026-04-01T00:00:00",  # naive — pre-SHA anchor
            "steps": {"graph": True},
        })
        self._make_child_no_sha(ws, "origin_bbbb2222", src_repo, src_dir)

        with patch(
            "terrain.foundation.services.git_service.GitChangeDetector"
        ) as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = 7
            instance.count_commits_since_sha.return_value = None
            instance.get_current_head.return_value = None
            entries = cli_mod._get_repo_status_entries(ws)

        by_dir = {e["artifact_dir"]: e for e in entries}
        child_entry = by_dir["origin_bbbb2222"]
        # No SHA anywhere → indexed_head stays None and legacy path runs.
        assert child_entry["indexed_head"] is None
        assert child_entry["commits_since"] == 7

    def test_child_falls_back_gracefully_when_source_meta_missing(
        self, tmp_path: Path
    ):
        """Edge: source artifact dir was deleted, or its meta.json is gone.

        The child entry must not raise; it falls back to its own values
        (effectively the pre-fix behavior). Degraded but alive.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        child_repo = tmp_path / "orphan_child"
        child_repo.mkdir()

        # Dangling source_artifact pointer — dir does not exist in ws.
        child_dir = ws / "origin_bbbb2222"
        _write_meta(child_dir, {
            "schema_version": 2,
            "source_artifact": "origin_nonexistent",
            "repo_path": normalize_repo_path(child_repo),
            "repo_name": child_repo.name,
            "linked_at": "2026-04-23T12:00:00+00:00",
            "indexed_at": "2026-04-23T12:00:00+00:00",
            "steps": {"graph": True},
        })

        with patch(
            "terrain.foundation.services.git_service.GitChangeDetector"
        ) as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = None
            instance.count_commits_since_sha.return_value = None
            instance.get_current_head.return_value = None
            # Must not raise.
            entries = cli_mod._get_repo_status_entries(ws)

        assert len(entries) == 1
        entry = entries[0]
        # No SHA anywhere → fallback produces unknown staleness, not a crash.
        assert entry["indexed_head"] is None
        assert entry["status"] == "unknown"

    def test_child_display_indexed_at_is_its_own_linked_at(
        self, tmp_path: Path
    ):
        """Child's own indexed_at / linked_at stays in the display field."""
        ws = tmp_path / "ws"
        ws.mkdir()
        src_repo = tmp_path / "origin"
        src_repo.mkdir()
        _git_init(src_repo)
        head = _git_commit(src_repo, "a.txt", "a", "first")

        src_dir = self._make_source_with_sha(
            ws, "origin_aaaa1111", src_repo, head
        )
        child_dir = self._make_child_no_sha(
            ws, "origin_bbbb2222", src_repo, src_dir
        )

        entries = cli_mod._get_repo_status_entries(ws)
        by_dir = {e["artifact_dir"]: e for e in entries}
        # Display indexed_at is the child's own link-time timestamp, so
        # operators can still see *when* the link was established.
        assert by_dir[child_dir.name]["indexed_at"] == "2026-04-23T12:00:00+00:00"
        assert by_dir[src_dir.name]["indexed_at"] == "2026-04-20T00:00:00+00:00"
