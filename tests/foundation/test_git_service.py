# terrain/tests/foundation/test_git_service.py
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from terrain.foundation.services.git_service import GitChangeDetector


@pytest.fixture
def detector():
    return GitChangeDetector()


def _mock_run(stdout: str, returncode: int = 0, stderr: str = ""):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class TestGetCurrentHead:
    def test_returns_commit_hash(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("abc1234\n")) as mock:
            head = detector.get_current_head(tmp_path)
        assert head == "abc1234"
        mock.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )

    def test_returns_none_for_non_git_repo(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("", returncode=128)):
            head = detector.get_current_head(tmp_path)
        assert head is None

    def test_returns_none_when_git_not_found(self, detector, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            head = detector.get_current_head(tmp_path)
        assert head is None


class TestGetChangedFiles:
    def test_returns_empty_when_no_last_commit(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("def5678\n")):
            files, head = detector.get_changed_files(tmp_path, last_commit=None)
        assert files == []
        assert head == "def5678"

    def test_returns_empty_when_head_unchanged(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("abc1234\n")):
            files, head = detector.get_changed_files(tmp_path, last_commit="abc1234")
        assert files == []
        assert head == "abc1234"

    def test_returns_changed_files(self, detector, tmp_path):
        (tmp_path / "foo.py").write_text("x=1")
        (tmp_path / "bar.py").write_text("y=2")

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("foo.py\nbar.py\n")

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="oldhead")

        assert head == "newhead"
        assert tmp_path / "foo.py" in files
        assert tmp_path / "bar.py" in files

    def test_includes_deleted_files(self, detector, tmp_path):
        """Deleted files (not on disk) are still returned so caller can remove them from graph."""
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("deleted.py\n")

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="oldhead")

        assert tmp_path / "deleted.py" in files

    def test_returns_none_files_when_commit_not_in_history(self, detector, tmp_path):
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("", returncode=128)  # git diff fails

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="ghostcommit")

        assert files is None  # Signal: full rebuild needed
        assert head == "newhead"

    def test_returns_empty_when_not_git_repo(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("", returncode=128)):
            files, head = detector.get_changed_files(tmp_path, last_commit="abc")
        assert files == []
        assert head is None

    def test_returns_none_when_diff_times_out(self, detector, tmp_path):
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            raise subprocess.TimeoutExpired(cmd, 10)

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="oldhead")

        assert files is None  # timeout => full rebuild needed
        assert head == "newhead"
