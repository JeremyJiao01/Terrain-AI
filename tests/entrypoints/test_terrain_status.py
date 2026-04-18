"""Tests for terrain status stale-repo detection."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terrain.entrypoints.cli.cli import _get_repo_status_entries
from terrain.foundation.services.git_service import GitChangeDetector


# ---------------------------------------------------------------------------
# GitChangeDetector.count_commits_since tests
# ---------------------------------------------------------------------------

class TestCountCommitsSince:
    def test_returns_zero_when_no_commits(self, tmp_path):
        detector = GitChangeDetector()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            count = detector.count_commits_since(tmp_path, "2026-04-01T00:00:00+00:00")
        assert count == 0

    def test_returns_commit_count(self, tmp_path):
        detector = GitChangeDetector()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="abc1234 fix something\ndef5678 add feature\n"
            )
            count = detector.count_commits_since(tmp_path, "2026-04-01T00:00:00+00:00")
        assert count == 2

    def test_returns_none_when_not_a_git_repo(self, tmp_path):
        detector = GitChangeDetector()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            count = detector.count_commits_since(tmp_path, "2026-04-01T00:00:00+00:00")
        assert count is None

    def test_returns_none_on_subprocess_error(self, tmp_path):
        detector = GitChangeDetector()
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("fail")):
            count = detector.count_commits_since(tmp_path, "2026-04-01T00:00:00+00:00")
        assert count is None

    def test_returns_none_on_timeout(self, tmp_path):
        detector = GitChangeDetector()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            count = detector.count_commits_since(tmp_path, "2026-04-01T00:00:00+00:00")
        assert count is None


# ---------------------------------------------------------------------------
# _get_repo_status_entries tests
# ---------------------------------------------------------------------------

class TestGetRepoStatusEntries:
    def _make_artifact_dir(self, ws: Path, name: str, repo_path: str, indexed_at: str) -> Path:
        artifact_dir = ws / name
        artifact_dir.mkdir()
        (artifact_dir / "meta.json").write_text(
            json.dumps({
                "repo_name": name,
                "repo_path": repo_path,
                "indexed_at": indexed_at,
            }),
            encoding="utf-8",
        )
        return artifact_dir

    def test_empty_workspace_returns_empty(self, tmp_path):
        entries = _get_repo_status_entries(tmp_path)
        assert entries == []

    def test_workspace_not_exist_returns_empty(self, tmp_path):
        entries = _get_repo_status_entries(tmp_path / "nonexistent")
        assert entries == []

    def test_up_to_date_repo(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        self._make_artifact_dir(ws, "myrepo_abc12345", str(repo), "2026-04-10T10:00:00+00:00")

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = 0
            entries = _get_repo_status_entries(ws)

        assert len(entries) == 1
        assert entries[0]["status"] == "up-to-date"
        assert entries[0]["commits_since"] == 0
        assert entries[0]["name"] == "myrepo_abc12345"

    def test_stale_repo(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        self._make_artifact_dir(ws, "myrepo_abc12345", str(repo), "2026-04-10T10:00:00+00:00")

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = 5
            entries = _get_repo_status_entries(ws)

        assert len(entries) == 1
        assert entries[0]["status"] == "stale"
        assert entries[0]["commits_since"] == 5

    def test_unknown_when_not_git_repo(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        self._make_artifact_dir(ws, "myrepo_abc12345", str(repo), "2026-04-10T10:00:00+00:00")

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = None
            entries = _get_repo_status_entries(ws)

        assert len(entries) == 1
        assert entries[0]["status"] == "unknown"
        assert entries[0]["commits_since"] is None

    def test_unknown_when_repo_path_missing(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        nonexistent_repo = str(tmp_path / "nonexistent")
        self._make_artifact_dir(ws, "gone_abc12345", nonexistent_repo, "2026-04-10T10:00:00+00:00")

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = None
            entries = _get_repo_status_entries(ws)

        assert len(entries) == 1
        assert entries[0]["status"] == "unknown"

    def test_multiple_repos(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        for name in ("alpha", "beta", "gamma"):
            repo = tmp_path / name
            repo.mkdir()
            self._make_artifact_dir(ws, f"{name}_deadbeef", str(repo), "2026-04-01T00:00:00+00:00")

        commit_counts = {"alpha_deadbeef": 0, "beta_deadbeef": 3, "gamma_deadbeef": None}

        def fake_count(repo_path: Path, indexed_at: str) -> int | None:
            for name, count in commit_counts.items():
                if name.split("_")[0] in str(repo_path):
                    return count
            return None

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.side_effect = fake_count
            entries = _get_repo_status_entries(ws)

        statuses = {e["name"].split("_")[0]: e["status"] for e in entries}
        assert statuses["alpha"] == "up-to-date"
        assert statuses["beta"] == "stale"
        assert statuses["gamma"] == "unknown"

    def test_skips_dirs_without_meta_json(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "no_meta_dir").mkdir()
        entries = _get_repo_status_entries(ws)
        assert entries == []


# ---------------------------------------------------------------------------
# GitChangeDetector.count_commits_since_sha — SHA-based counting
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)


def _commit(path: Path, name: str, content: str, msg: str) -> str:
    (path / name).write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return head


class TestCountCommitsSinceSha:
    def test_zero_when_head_equals_sha(self, tmp_path):
        _init_repo(tmp_path)
        head = _commit(tmp_path, "a.txt", "a", "first")
        assert GitChangeDetector().count_commits_since_sha(tmp_path, head) == 0

    def test_counts_commits_ahead_of_sha(self, tmp_path):
        _init_repo(tmp_path)
        anchor = _commit(tmp_path, "a.txt", "a", "first")
        _commit(tmp_path, "b.txt", "b", "second")
        _commit(tmp_path, "c.txt", "c", "third")
        assert GitChangeDetector().count_commits_since_sha(tmp_path, anchor) == 2

    def test_zero_after_amend(self, tmp_path):
        """git commit --amend rewrites the commit timestamp but same SHA semantics.

        Under the old `--since` strategy, the amended commit's new timestamp
        would be counted as a change. SHA-based counting reports 0 because
        HEAD's new SHA is what we would now record as last_indexed_commit.
        """
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "a", "first")
        # Amend — rewrites the commit (new SHA, new timestamp).
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit", "--date=now"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()
        # Indexing captures the post-amend SHA — staleness should be 0.
        assert GitChangeDetector().count_commits_since_sha(tmp_path, new_head) == 0

    def test_none_when_sha_missing(self, tmp_path):
        """Force-push / rebase dropped the anchor SHA — must return None, not silently miscount."""
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "a", "first")
        bogus = "0" * 40
        assert GitChangeDetector().count_commits_since_sha(tmp_path, bogus) is None

    def test_none_for_empty_sha(self, tmp_path):
        assert GitChangeDetector().count_commits_since_sha(tmp_path, "") is None

    def test_none_on_timeout(self, tmp_path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            assert GitChangeDetector().count_commits_since_sha(tmp_path, "abc1234") is None


# ---------------------------------------------------------------------------
# _get_repo_status_entries integration — SHA-preferred + legacy fallback
# ---------------------------------------------------------------------------

class TestStatusEntriesShaPath:
    def test_sha_path_reports_up_to_date(self, tmp_path):
        """When last_indexed_commit matches HEAD, status is up-to-date."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _init_repo(repo)
        head = _commit(repo, "a.txt", "a", "first")

        artifact_dir = ws / "myrepo_abc12345"
        artifact_dir.mkdir()
        (artifact_dir / "meta.json").write_text(json.dumps({
            "repo_name": "myrepo_abc12345",
            "repo_path": str(repo),
            "indexed_at": "2026-04-10T10:00:00+00:00",
            "last_indexed_commit": head,
        }))

        entries = _get_repo_status_entries(ws)
        assert entries[0]["status"] == "up-to-date"
        assert entries[0]["commits_since"] == 0

    def test_sha_path_reports_n_commits_ahead(self, tmp_path):
        """HEAD is 3 commits past last_indexed_commit → stale, commits_since==3."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _init_repo(repo)
        anchor = _commit(repo, "a.txt", "a", "first")
        _commit(repo, "b.txt", "b", "second")
        _commit(repo, "c.txt", "c", "third")
        _commit(repo, "d.txt", "d", "fourth")

        artifact_dir = ws / "myrepo_abc12345"
        artifact_dir.mkdir()
        (artifact_dir / "meta.json").write_text(json.dumps({
            "repo_name": "myrepo_abc12345",
            "repo_path": str(repo),
            "indexed_at": "2026-04-10T10:00:00+00:00",
            "last_indexed_commit": anchor,
        }))

        entries = _get_repo_status_entries(ws)
        assert entries[0]["status"] == "stale"
        assert entries[0]["commits_since"] == 3

    def test_sha_path_unknown_when_sha_force_pushed_away(self, tmp_path):
        """Simulate force-push: last_indexed_commit no longer exists in repo."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _init_repo(repo)
        _commit(repo, "a.txt", "a", "first")

        artifact_dir = ws / "myrepo_abc12345"
        artifact_dir.mkdir()
        (artifact_dir / "meta.json").write_text(json.dumps({
            "repo_name": "myrepo_abc12345",
            "repo_path": str(repo),
            "indexed_at": "2026-04-10T10:00:00+00:00",
            "last_indexed_commit": "0" * 40,  # SHA that no longer exists
        }))

        entries = _get_repo_status_entries(ws)
        assert entries[0]["status"] == "unknown"
        assert entries[0]["commits_since"] is None

    def test_legacy_fallback_when_last_indexed_commit_missing(self, tmp_path):
        """Old meta.json without last_indexed_commit — naive indexed_at still works via --since fallback."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        repo = tmp_path / "myrepo"
        repo.mkdir()
        # Legacy entry: no last_indexed_commit, naive timestamp
        artifact_dir = ws / "myrepo_abc12345"
        artifact_dir.mkdir()
        (artifact_dir / "meta.json").write_text(json.dumps({
            "repo_name": "myrepo_abc12345",
            "repo_path": str(repo),
            "indexed_at": "2026-04-10T10:00:00",  # naive legacy string
        }))

        with patch("terrain.foundation.services.git_service.GitChangeDetector") as MockDetector:
            instance = MockDetector.return_value
            instance.count_commits_since.return_value = 2
            entries = _get_repo_status_entries(ws)

        # Fallback went through count_commits_since with the legacy string
        instance.count_commits_since.assert_called_once()
        assert entries[0]["status"] == "stale"
        assert entries[0]["commits_since"] == 2


# ---------------------------------------------------------------------------
# save_meta writes timezone-aware UTC for indexed_at
# ---------------------------------------------------------------------------

class TestSaveMetaIndexedAtAware:
    def test_indexed_at_is_timezone_aware_utc(self, tmp_path):
        from datetime import datetime
        from terrain.entrypoints.mcp.pipeline import save_meta

        save_meta(tmp_path, tmp_path / "repo", wiki_page_count=0)
        meta = json.loads((tmp_path / "meta.json").read_text())
        parsed = datetime.fromisoformat(meta["indexed_at"])
        assert parsed.tzinfo is not None, (
            f"indexed_at must be timezone-aware, got naive: {meta['indexed_at']!r}"
        )
        # And it should be UTC (offset == 0)
        assert parsed.utcoffset().total_seconds() == 0
