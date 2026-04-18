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

    def get_merge_commits(self, repo_path: Path, limit: int = 2, branch: str | None = None) -> list[str]:
        """Return the last *limit* merge commit SHAs (most recent first).

        If *branch* is given, look at that branch's history instead of HEAD.
        Returns an empty list if the repo has no merge commits or is not a
        git repository.
        """
        try:
            cmd = ["git", "log", "--merges", f"-{limit}", "--format=%H"]
            if branch:
                cmd.append(branch)
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug("git log --merges failed: {}", result.stderr.strip())
                return []
            return [sha.strip() for sha in result.stdout.strip().splitlines() if sha.strip()]
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.debug("get_merge_commits failed: {}", e)
            return []

    def count_commits_since(self, repo_path: Path, since_iso: str) -> int | None:
        """Return the number of commits in *repo_path* made after *since_iso*.

        Args:
            repo_path: Path to the git repository.
            since_iso: ISO 8601 timestamp (e.g. ``"2026-04-16T08:22:51+00:00"``).

        Returns:
            Number of commits (0 means up-to-date), or ``None`` if *repo_path*
            is not a git repository or the command fails.

        Note:
            ``git log --since`` interprets naive timestamps in *local* time and
            silently mis-counts across timezone moves, rebases, or clock skew.
            Prefer :meth:`count_commits_since_sha` when a stable anchor SHA
            is available.
        """
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_iso}", "--oneline"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug("git log --since failed (exit {}): {}", result.returncode, result.stderr.strip())
                return None
            lines = [l for l in result.stdout.splitlines() if l.strip()]
            return len(lines)
        except subprocess.TimeoutExpired as e:
            logger.debug("git log --since timed out: {}", e)
            return None
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.debug("count_commits_since failed: {}", e)
            return None

    def count_commits_since_sha(self, repo_path: Path, sha: str) -> int | None:
        """Return the number of commits reachable from HEAD but not from *sha*.

        This is immune to timezone shifts, clock skew, and commit-date
        rewrites (rebase, cherry-pick, amend) that plague timestamp-based
        counting. Shallow clones may undercount — that's acceptable.

        Args:
            repo_path: Path to the git repository.
            sha: An anchor commit SHA previously captured at index time.

        Returns:
            - non-negative int — commit count between *sha* and HEAD
            - ``None`` — *sha* missing from history (force-push / shallow clone),
              *sha* empty, not a git repository, or subprocess failure.
              Caller should surface this as "unknown", not a silent zero.
        """
        if not sha:
            return None
        try:
            # Verify the SHA exists in this repo before counting.
            verify = subprocess.run(
                ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if verify.returncode != 0:
                logger.debug("anchor SHA {} not in repo", sha[:8])
                return None

            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD", f"^{sha}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug(
                    "git rev-list --count HEAD ^{} failed (exit {}): {}",
                    sha[:8], result.returncode, result.stderr.strip(),
                )
                return None
            return int(result.stdout.strip())
        except ValueError as e:
            logger.debug("rev-list output not an int: {}", e)
            return None
        except subprocess.TimeoutExpired as e:
            logger.debug("count_commits_since_sha timed out: {}", e)
            return None
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.debug("count_commits_since_sha failed: {}", e)
            return None

    def get_changed_files_between(
        self,
        repo_path: Path,
        from_commit: str,
        to_commit: str,
    ) -> list[Path] | None:
        """Return files changed between *from_commit* and *to_commit*.

        Returns:
            - list of changed absolute :class:`Path` objects (may be empty)
            - ``None`` if either commit is not in git history or the diff fails
        """
        try:
            result = subprocess.run(
                ["git", "diff", from_commit, to_commit, "--name-only"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(
                    "git diff {} {} failed (exit {}): {}",
                    from_commit[:8],
                    to_commit[:8],
                    result.returncode,
                    result.stderr.strip(),
                )
                return None

            changed: list[Path] = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    changed.append(repo_path / line)
            return changed

        except subprocess.TimeoutExpired as e:
            logger.warning("git diff timed out: {}", e)
            return None
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git diff failed: {}", e)
            return None
