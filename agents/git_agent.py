"""
Git commit + GitHub PR auto-creation agent.

Runs after P8c completes:
  1. Initialises (or opens) a local git repo inside the project output dir
  2. Stages all generated artefacts
  3. Creates a commit: "[AI] Silicon to Software (S2S): <project_name> — P8c complete"
  4. If GITHUB_TOKEN + GITHUB_REPO are set, pushes branch and opens a PR

Configuration (in .env):
    GITHUB_TOKEN      — fine-grained or classic PAT with repo scope
    GITHUB_REPO       — "owner/repo"   (e.g. "acme/hardware-pipeline-demo")
    GITHUB_REPO_URL   — HTTPS clone URL (auto-derived from GITHUB_REPO if omitted)
    GIT_ENABLED       — "true" / "false"  (default: true when token is present)
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config import settings

logger = logging.getLogger(__name__)


class GitAgent:
    """Lightweight Git + GitHub PR integration for P8c post-processing."""

    def __init__(self):
        self.enabled = bool(settings.github_token) and settings.git_enabled
        self._github_client = None

        if self.enabled:
            try:
                from github import Github
                self._github_client = Github(settings.github_token)
                logger.info("GitAgent: GitHub client initialised")
            except ImportError:
                logger.warning("GitAgent: PyGithub not installed — PR creation disabled")
                self._github_client = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def commit_and_pr(
        self,
        project_name: str,
        output_dir: Path,
        review_report_path: Optional[Path] = None,
        pr_body_extra: str = "",
    ) -> Dict:
        """
        Main entry point.
        Returns a dict with keys: success, commit_sha, pr_url, error.
        """
        if not self.enabled:
            return {
                "success": False,
                "reason": "Git integration disabled — set GITHUB_TOKEN in .env",
                "commit_sha": None,
                "pr_url": None,
            }

        try:
            repo_path = self._ensure_repo(output_dir)

            # Ensure the remote GitHub repo has a base branch before creating PRs.
            # On first push to an empty GitHub repo, we push main first so PRs have a base.
            if self._github_client and settings.github_repo:
                self._ensure_remote_base_branch(repo_path)

            branch = self._make_branch_name(project_name)
            self._create_branch(repo_path, branch)
            commit_sha = self._stage_and_commit(repo_path, project_name)

            pr_url = None
            push_error = ""
            pr_error = ""
            remote_status = "ok"
            if not (self._github_client and settings.github_repo):
                # Truly no remote configured — token missing OR github_repo
                # blank OR PyGithub not installed.
                remote_status = "no_remote"
                if not settings.github_repo:
                    push_error = "GITHUB_REPO not set in .env"
                elif not self._github_client:
                    push_error = "PyGithub not installed (run: pip install PyGithub)"
            else:
                push_ok, push_error = self._push_branch(repo_path, branch)
                if push_ok:
                    pr_url = self._create_pr(
                        project_name=project_name,
                        branch=branch,
                        review_report_path=review_report_path,
                        pr_body_extra=pr_body_extra,
                    )
                    if not pr_url:
                        # Push succeeded but PR creation didn't — could be
                        # the org's branch protection or the source branch
                        # doesn't differ from base. Either way, surface it.
                        pr_error = (
                            "PR creation failed after a successful push — "
                            "check the backend logs for the GitHub API "
                            "error (most often: branch protection rules "
                            "block PR creation, or the head branch "
                            "matches base with no diff)."
                        )
                        remote_status = "push_ok_pr_failed"
                else:
                    remote_status = "push_failed"

            logger.info(f"GitAgent: commit {commit_sha} on branch {branch}")
            return {
                "success": True,
                "commit_sha": commit_sha,
                "branch": branch,
                "pr_url": pr_url,
                "push_error": push_error,
                "pr_error": pr_error,
                "remote_status": remote_status,
                "repo_path": str(repo_path),
            }

        except Exception as e:
            logger.error(f"GitAgent: commit_and_pr failed: {e}")
            return {"success": False, "error": str(e), "commit_sha": None, "pr_url": None}

    # ------------------------------------------------------------------ #
    # Git helpers
    # ------------------------------------------------------------------ #

    def _ensure_repo(self, output_dir: Path) -> Path:
        """
        Init a git repo INSIDE output_dir (not a parent).
        Uses output_dir/.git — never walks up to the project root repo.
        """
        import git as gitlib

        output_dir.mkdir(parents=True, exist_ok=True)

        # Only look for a repo directly in output_dir — NOT parent directories.
        # search_parent_directories=True would accidentally find the project root .git
        # and commit generated artefacts there instead of the output repo.
        try:
            repo = gitlib.Repo(str(output_dir))  # strict: no parent search
            logger.info(f"GitAgent: using existing output repo at {output_dir}")
        except gitlib.InvalidGitRepositoryError:
            repo = gitlib.Repo.init(str(output_dir))
            logger.info(f"GitAgent: initialized new output repo at {output_dir}")

        # Set minimal git config so commits don't fail
        with repo.config_writer() as cfg:
            cfg.set_value("user", "name", "Silicon to Software (S2S) AI")
            cfg.set_value("user", "email", "ai@hardware-pipeline.local")

        # Set/update remote URL with embedded auth token
        if settings.github_repo and settings.github_token:
            remote_url = settings.github_repo_url or \
                f"https://{settings.github_token}@github.com/{settings.github_repo}.git"
            try:
                repo.remote("origin").set_url(remote_url)
                logger.info("GitAgent: updated origin remote URL")
            except Exception:
                try:
                    repo.create_remote("origin", remote_url)
                    logger.info("GitAgent: created origin remote")
                except Exception as e:
                    logger.warning(f"GitAgent: could not set remote: {e}")

        return output_dir

    def _ensure_remote_base_branch(self, repo_path: Path) -> None:
        """
        If the GitHub repo has no branches (completely empty), push an initial commit
        to 'main' so that subsequent feature-branch PRs have a base to target.
        Only runs once — skipped if the remote already has branches.
        """
        import git as gitlib
        try:
            gh_repo = self._github_client.get_repo(settings.github_repo)
            branches = list(gh_repo.get_branches())
            if branches:
                return  # Remote already has content — nothing to do

            # Remote is empty: push whatever HEAD is (initial commit) to 'main'
            repo = gitlib.Repo(str(repo_path))
            if not repo.heads:
                # Create the initial commit if it doesn't exist yet
                repo.index.commit(
                    "chore: init silicon to software (s2s) output repo",
                    author=gitlib.Actor("Silicon to Software (S2S) AI", "ai@hardware-pipeline.local"),
                    committer=gitlib.Actor("Silicon to Software (S2S) AI", "ai@hardware-pipeline.local"),
                )

            # Ensure local branch is named 'main'
            try:
                _ = repo.create_head("main", repo.head.commit)
            except gitlib.GitCommandError:
                _ = repo.heads["main"] if "main" in [h.name for h in repo.heads] else repo.heads[0]

            try:
                origin = repo.remote("origin")
                origin.push(refspec="main:main")
                logger.info("GitAgent: pushed initial 'main' branch to remote GitHub repo")
            except Exception as e:
                logger.warning(f"GitAgent: initial main push failed: {e}")

        except Exception as e:
            logger.warning(f"GitAgent: _ensure_remote_base_branch failed: {e}")

    def _make_branch_name(self, project_name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", project_name).strip("-").lower()
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M")
        return f"ai/pipeline/{slug}-{ts}"

    def _create_branch(self, repo_path: Path, branch: str) -> None:
        import git as gitlib
        repo = gitlib.Repo(str(repo_path))

        # Need at least one commit for branch creation
        if not repo.heads:
            # Create an empty initial commit
            repo.index.commit(
                "chore: init silicon to software (s2s) output repo",
                author=gitlib.Actor("Silicon to Software (S2S) AI", "ai@hardware-pipeline.local"),
                committer=gitlib.Actor("Silicon to Software (S2S) AI", "ai@hardware-pipeline.local"),
            )

        # Create and checkout the branch
        try:
            new_branch = repo.create_head(branch)
            new_branch.checkout()
        except gitlib.GitCommandError as e:
            logger.warning(f"Branch creation warning: {e}")

    def _stage_and_commit(self, repo_path: Path, project_name: str) -> str:
        import git as gitlib
        repo = gitlib.Repo(str(repo_path))

        # Stage all untracked + modified files
        repo.git.add(A=True)

        if not repo.index.diff("HEAD") and not repo.untracked_files:
            logger.info("GitAgent: nothing to commit")
            return repo.head.commit.hexsha if repo.heads else "no-changes"

        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"[AI] Silicon to Software (S2S): {project_name} — P8c complete\n\n"
            f"Generated by Silicon to Software (S2S) v2 on {ts}.\n"
            f"Includes: device drivers, Qt GUI skeleton, SRS/SDD/HRS documents, "
            f"compliance report, netlist, GLR spec, code review report.\n\n"
            f"🤖 Auto-committed by Silicon to Software (S2S) AI"
        )

        actor = gitlib.Actor("Silicon to Software (S2S) AI", "ai@hardware-pipeline.local")
        commit = repo.index.commit(msg, author=actor, committer=actor)
        return commit.hexsha[:10]

    def _push_branch(self, repo_path: Path, branch: str) -> tuple[bool, str]:
        """Push the branch. Returns (ok, error_message).

        The push BYPASSES the user's git credential helper (Git
        Credential Manager / GCM on Windows, libsecret on Linux,
        osxkeychain on macOS) by setting `credential.helper=` empty
        for this single command. Without this, GCM intercepts the
        push, ignores the embedded `https://TOKEN@github.com/...`
        URL, and pops up an OAuth dialog targeted at the
        `git-ecosystem/git-credential-manager` GitHub App — which
        the user reported as the "ecosystem" prompt blocking P8c.

        We also disable interactive credential prompting via
        `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never` so that
        even if the helper somehow runs, it can't pause the agent
        waiting for human input.

        The error message is propagated to git_summary.md so the user
        can see the ACTUAL push failure if any (most often: the token
        lacks the `workflow` scope when P8c includes a
        `.github/workflows/...` file).
        """
        import os
        import git as gitlib
        try:
            repo = gitlib.Repo(str(repo_path))

            # Make absolutely sure no interactive prompt can fire from
            # any helper that the GitPython invocation spawns.
            push_env = {
                "GIT_TERMINAL_PROMPT": "0",      # disable terminal prompt
                "GCM_INTERACTIVE":     "never",  # disable Git Credential Manager UI
                "GIT_ASKPASS":         "echo",   # neutralise askpass helper
            }

            # `-c credential.helper=` (empty value) tells git to skip
            # ALL credential helpers for this one invocation. The token
            # embedded in the remote URL is then the sole auth path.
            # The repo.git.<command> form lets us pass `-c` flags and
            # custom env safely without shelling out manually.
            # Toggleable via `GIT_BYPASS_CREDENTIAL_HELPER=false` in
            # `.env` for the rare case the user genuinely wants GCM.
            cmd = ["git"]
            if settings.git_bypass_credential_helper:
                cmd += [
                    "-c", "credential.helper=",
                    "-c", "credential.useHttpPath=true",
                ]
            cmd += ["push", "origin", f"{branch}:{branch}"]
            repo.git.execute(
                cmd,
                env={**os.environ, **push_env},
            )
            return True, ""
        except Exception as e:
            err_text = str(e)
            logger.warning(f"GitAgent: push failed: {err_text}")
            # Detect the most common scope error and rewrite to a
            # human-actionable message. GitHub's own message is fine
            # but verbose — we keep it AND add the fix instruction.
            hint = ""
            if "workflow" in err_text and "scope" in err_text:
                hint = (
                    " — your GITHUB_TOKEN is missing the `workflow` "
                    "scope. P8c writes `.github/workflows/*.yml` and "
                    "GitHub blocks tokens without `workflow` scope from "
                    "creating those files. Re-issue the PAT with both "
                    "`repo` AND `workflow` scopes ticked."
                )
            elif "Authentication failed" in err_text or "401" in err_text:
                hint = (
                    " — token is invalid or expired. Re-issue your "
                    "GITHUB_TOKEN at https://github.com/settings/tokens "
                    "with `repo` and `workflow` scopes."
                )
            elif "403" in err_text:
                hint = (
                    " — GitHub returned 403 Forbidden. Likely causes: "
                    "(a) repo is part of an org with SAML SSO not yet "
                    "authorised for this token, (b) branch protection "
                    "blocks the push, or (c) token lacks required scope."
                )
            elif (
                "git-ecosystem" in err_text.lower()
                or "credential manager" in err_text.lower()
                or "could not read username" in err_text.lower()
            ):
                # Fallback for the case the bypass didn't take (very
                # old git, or an env that ignores -c overrides).
                hint = (
                    " — Git Credential Manager (GCM) intercepted the "
                    "push and tried to open an OAuth dialog for the "
                    "git-ecosystem app instead of using the embedded "
                    "PAT. Workaround: in .env set "
                    "GIT_BYPASS_CREDENTIAL_HELPER=true, restart the "
                    "FastAPI server, then re-run P8c. Persistent fix: "
                    "globally disable GCM for this account "
                    "(`git config --global credential.helper`)."
                )
            return False, err_text + hint

    # ------------------------------------------------------------------ #
    # GitHub PR
    # ------------------------------------------------------------------ #

    def _create_pr(
        self,
        project_name: str,
        branch: str,
        review_report_path: Optional[Path],
        pr_body_extra: str,
    ) -> Optional[str]:
        if not self._github_client or not settings.github_repo:
            return None

        try:
            gh_repo = self._github_client.get_repo(settings.github_repo)

            # Try to get default branch
            default_branch = gh_repo.default_branch or "main"

            # Build PR body
            review_summary = ""
            if review_report_path and review_report_path.exists():
                text = review_report_path.read_text(encoding="utf-8")
                # Pull first 1500 chars (executive summary)
                review_summary = text[:1500]

            ts_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            review_block = review_summary[:1200] if review_summary else "See code_review_report.md"
            extra_section = ("---\n" + pr_body_extra) if pr_body_extra else ""
            body = (
                "## Silicon to Software (S2S) AI — Auto-generated PR\n\n"
                f"**Project:** {project_name}\n"
                "**Phase completed:** P8c (Code Review)\n"
                f"**Generated:** {ts_str}\n\n"
                "---\n\n"
                "### Artifacts Included\n"
                "- \u2705 Device driver source files (MISRA-C compliant)\n"
                "- \u2705 PySide6 Qt GUI application skeleton\n"
                "- \u2705 Unit test suite\n"
                "- \u2705 SRS / SDD / HRS documents (IEEE compliant)\n"
                "- \u2705 Compliance report (RoHS/REACH/FCC) + CycloneDX SBOM\n"
                "- \u2705 Logical netlist\n"
                "- \u2705 Code review report (Cppcheck + Lizard + MISRA-C)\n\n"
                "---\n\n"
                "### Code Review Summary\n\n"
                "```\n"
                f"{review_block}\n"
                "```\n\n"
                f"{extra_section}\n\n"
                "---\n"
                "_\U0001f916 This PR was automatically created by "
                "[Silicon to Software (S2S) v2](http://localhost:8000/app)_\n"
            )

            pr = gh_repo.create_pull(
                title=f"[AI] {project_name} — Silicon to Software (S2S) P8c complete",
                body=body,
                head=branch,
                base=default_branch,
            )
            logger.info(f"GitAgent: PR created: {pr.html_url}")
            return pr.html_url

        except Exception as e:
            logger.error(f"GitAgent: PR creation failed: {type(e).__name__}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def get_status(self) -> Dict:
        return {
            "enabled": self.enabled,
            "github_token_set": bool(settings.github_token),
            "github_repo": settings.github_repo or "not configured",
            "pr_creation_available": self._github_client is not None,
        }
