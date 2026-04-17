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
