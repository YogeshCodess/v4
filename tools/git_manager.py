"""
GitManager — programmatic Git operations for versioning pipeline outputs.

Uses GitPython. Falls back gracefully if git is unavailable or not in a repo.

Usage:
    gm = GitManager(repo_path=Path("output/my_project"))
    gm.init()
    gm.add_and_commit("Generated HRS document (P2)", files=["HRS_my_project.md"])
    gm.tag("phase-2-complete")
    log = gm.log(n=5)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

try:
    import git as gitpython
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    log.info("GitPython not installed — git_manager disabled (pip install gitpython)")


class GitManager:
    """Wraps GitPython for pipeline output versioning."""

    def __init__(self, repo_path: Path | str):
        self._path = Path(repo_path)
        self._repo: Optional["gitpython.Repo"] = None
        if GIT_AVAILABLE:
            self._open_or_none()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _open_or_none(self) -> None:
        """Try to open an existing repo; set to None if not found."""
        try:
            self._repo = gitpython.Repo(self._path, search_parent_directories=False)
        except Exception:
            self._repo = None

    def init(self) -> bool:
        """Initialize a new git repo at repo_path (idempotent)."""
        if not GIT_AVAILABLE:
            return False
        try:
            if self._repo is None:
                self._repo = gitpython.Repo.init(self._path)
                log.info("git.init path=%s", self._path)
            return True
        except Exception as exc:
            log.warning("git.init_failed: %s", exc)
            return False

    def is_available(self) -> bool:
        return GIT_AVAILABLE and self._repo is not None

    # ── Core operations ───────────────────────────────────────────────────────

    def add_and_commit(
        self,
        message: str,
        files: Optional[list[str]] = None,
        author_name: str = "Silicon to Software (S2S) AI",
        author_email: str = "pipeline@hardware-ai.local",
    ) -> Optional[str]:
        """
        Stage files and create a commit.

        Args:
            message: Commit message.
            files: List of relative file paths to stage. Stages all changes if None.
            author_name / author_email: Git author identity.

        Returns:
            Commit SHA hex string, or None on failure.
        """
        if not self.is_available():
            return None
        try:
            repo = self._repo
            if files:
                repo.index.add([str(f) for f in files])
            else:
                repo.git.add(A=True)

            if not repo.index.diff("HEAD") and not repo.untracked_files:
                log.debug("git.commit_skipped: nothing to commit")
                return None

            actor = gitpython.Actor(author_name, author_email)
            commit = repo.index.commit(
                message,
                author=actor,
                committer=actor,
            )
            log.info("git.committed sha=%s msg=%r", commit.hexsha[:8], message[:60])
            return commit.hexsha
        except Exception as exc:
            log.warning("git.commit_failed: %s", exc)
            return None

    def tag(self, tag_name: str, message: str = "") -> bool:
        """Create a lightweight or annotated tag on HEAD."""
        if not self.is_available():
            return False
        try:
            repo = self._repo
            if message:
                repo.create_tag(tag_name, message=message)
            else:
                repo.create_tag(tag_name)
            log.info("git.tagged tag=%s", tag_name)
            return True
        except Exception as exc:
            log.warning("git.tag_failed tag=%s: %s", tag_name, exc)
            return False

    def log(self, n: int = 10) -> list[dict]:
        """Return the last n commits as a list of dicts."""
        if not self.is_available():
            return []
        try:
            commits = []
            for c in list(self._repo.iter_commits(max_count=n)):
                commits.append({
                    "sha": c.hexsha[:8],
                    "message": c.message.strip().split("\n")[0],
                    "author": str(c.author),
                    "date": c.committed_datetime.isoformat(),
                })
            return commits
        except Exception as exc:
            log.warning("git.log_failed: %s", exc)
            return []

    def diff_summary(self) -> str:
        """Return a short diff summary of the working tree vs HEAD."""
        if not self.is_available():
            return ""
        try:
            return self._repo.git.diff("--stat", "HEAD")
        except Exception:
            return ""
