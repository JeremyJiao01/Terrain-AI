"""Git-based change detection for incremental graph updates."""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger


class GitChangeDetector:
    """Detect changed files between two git commits."""

    def get_current_head(self, repo_path: Path) -> str | None:
        """Return the current HEAD commit hash, or None if not a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.debug("git rev-parse HEAD failed: {}", e)
        return None

    def get_changed_files(
        self,
        repo_path: Path,
        last_commit: str | None,
    ) -> tuple[list[Path] | None, str | None]:
        """Return (changed_files, current_head).

        Returns:
            - ([], None)         — not a git repo
            - ([], current_head) — no last_commit (first index) or no changes
            - ([...], current_head) — list of changed/deleted file paths
            - (None, current_head)  — last_commit not in git history; caller should full-rebuild
        """
        current_head = self.get_current_head(repo_path)
        if current_head is None:
            return [], None  # Not a git repo

        if last_commit is None:
            return [], current_head  # First-time index, no incremental to do

        if last_commit == current_head:
            return [], current_head  # Nothing changed

        try:
            result = subprocess.run(
                ["git", "diff", last_commit, current_head, "--name-only"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "git diff {} {} failed (exit {}): {}",
                    last_commit[:8], current_head[:8],
                    result.returncode, result.stderr.strip(),
                )
                return None, current_head

            changed: list[Path] = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    changed.append(repo_path / line)

            return changed, current_head

        except subprocess.TimeoutExpired as e:
            logger.warning("git diff timed out: {}", e)
            return None, current_head
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git diff failed: {}", e)
            return None, current_head
