"""Tests that CLI meta.json readers survive non-UTF-8 bytes.

Reproduces JER-93: a single corrupt meta.json crashes `terrain status` /
`terrain list` with UnicodeDecodeError. The MCP side already handles this
via errors="replace" + UnicodeDecodeError in except — the CLI side must
mirror the same defense.
"""
from __future__ import annotations

import json
from pathlib import Path

from terrain.entrypoints.cli.cli import (
    _get_repo_status_entries,
    _load_repos,
    _rename_repo,
)


def _write_good_meta(artifact_dir: Path, name: str, repo_path: Path) -> None:
    artifact_dir.mkdir()
    (artifact_dir / "meta.json").write_text(
        json.dumps({
            "repo_name": name,
            "repo_path": str(repo_path),
            "indexed_at": "2026-04-10T10:00:00+00:00",
        }),
        encoding="utf-8",
    )


def _write_bad_meta(artifact_dir: Path) -> None:
    artifact_dir.mkdir()
    (artifact_dir / "meta.json").write_bytes(b'{"repo_name":"\xff\xfe bad"}')


class TestLoadReposBadEncoding:
    def test_load_repos_does_not_crash_on_bad_meta(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_bad_meta(ws / "broken_abc12345")
        # Must not raise UnicodeDecodeError.
        repos = _load_repos(ws)
        # Entry may be skipped (JSON decode fails after replace) or parsed.
        # Either outcome is acceptable — crash is not.
        assert isinstance(repos, list)

    def test_load_repos_skips_bad_entry_keeps_good_ones(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        good_repo = tmp_path / "goodrepo"
        good_repo.mkdir()
        _write_good_meta(ws / "good_abc12345", "goodrepo", good_repo)
        _write_bad_meta(ws / "broken_def67890")

        repos = _load_repos(ws)
        names = [r["name"] for r in repos]
        assert "goodrepo" in names

    def test_load_repos_survives_bad_active_txt(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        good_repo = tmp_path / "goodrepo"
        good_repo.mkdir()
        _write_good_meta(ws / "good_abc12345", "goodrepo", good_repo)
        # Active pointer with a stray non-UTF-8 byte.
        (ws / "active.txt").write_bytes(b"ok\xff\n")

        repos = _load_repos(ws)
        assert len(repos) == 1
        assert repos[0]["name"] == "goodrepo"


class TestGetRepoStatusEntriesBadEncoding:
    def test_status_entries_does_not_crash_on_bad_meta(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_bad_meta(ws / "broken_abc12345")
        entries = _get_repo_status_entries(ws)
        assert isinstance(entries, list)

    def test_status_entries_skips_bad_entry_keeps_good_ones(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        good_repo = tmp_path / "goodrepo"
        good_repo.mkdir()
        _write_good_meta(ws / "good_abc12345", "goodrepo", good_repo)
        _write_bad_meta(ws / "broken_def67890")

        entries = _get_repo_status_entries(ws)
        names = [e["name"] for e in entries]
        assert "goodrepo" in names


class TestRenameRepoBadEncoding:
    def test_rename_repo_does_not_crash_on_bad_meta(self, tmp_path):
        artifact_dir = tmp_path / "broken_abc12345"
        _write_bad_meta(artifact_dir)
        # Should return silently, not raise.
        _rename_repo(artifact_dir, "newname")
