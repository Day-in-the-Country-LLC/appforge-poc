"""Git operations for workspace management."""

import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import structlog

logger = structlog.get_logger(__name__)


class GitOps:
    """Manages git operations for agent workspaces."""

    def __init__(self, workspace_root: str):
        """Initialize git operations.

        Args:
            workspace_root: Root directory for all workspaces
        """
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        """Get the worktree path for an issue.

        Args:
            repo_name: Repository name
            issue_number: GitHub issue number

        Returns:
            Path to the worktree
        """
        return self.workspace_root / "worktrees" / repo_name / str(issue_number)

    def get_branch_name(self, issue_number: int, slug: str) -> str:
        """Get the branch name for an issue.

        Args:
            issue_number: GitHub issue number
            slug: Issue slug (from title)

        Returns:
            Branch name in format agent/<issue#>-<slug>
        """
        return f"agent/{issue_number}-{slug}"

    def _sanitize_repo_url(self, repo_url: str) -> str:
        """Redact credentials from repo URLs before logging."""
        parts = urlsplit(repo_url)
        if not parts.username and not parts.password:
            return repo_url

        hostname = parts.hostname or ""
        if parts.port:
            hostname = f"{hostname}:{parts.port}"

        user = parts.username or ""
        redacted = f"{user}:***@" if user else "***@"
        netloc = f"{redacted}{hostname}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    async def clone_repo(
        self,
        repo_url: str,
        repo_name: str,
        issue_number: int,
    ) -> Path:
        """Clone a repository into a worktree.

        Args:
            repo_url: Repository URL
            repo_name: Repository name
            issue_number: GitHub issue number

        Returns:
            Path to the cloned repository
        """
        worktree_path = self.get_worktree_path(repo_name, issue_number)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        safe_repo_url = self._sanitize_repo_url(repo_url)
        logger.info("cloning_repo", repo_url=safe_repo_url, worktree_path=str(worktree_path))

        try:
            subprocess.run(
                ["git", "clone", repo_url, str(worktree_path)],
                check=True,
                capture_output=True,
                timeout=300,
            )
            logger.info("repo_cloned", worktree_path=str(worktree_path))
            return worktree_path
        except subprocess.CalledProcessError as e:
            logger.error(
                "clone_failed",
                returncode=e.returncode,
                stderr=e.stderr.decode(),
            )
            raise

    async def ensure_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        base_branch: str = "main",
    ) -> None:
        """Ensure the branch exists and is checked out in the worktree.

        Args:
            worktree_path: Path to the worktree
            branch_name: Name of the branch to create or checkout
            base_branch: Base branch to branch from (default: main)
        """
        logger.info(
            "ensuring_branch",
            branch=branch_name,
            base_branch=base_branch,
            worktree=str(worktree_path),
        )

        try:
            subprocess.run(
                ["git", "-C", str(worktree_path), "fetch", "origin", "--prune"],
                check=True,
                capture_output=True,
                timeout=120,
            )

            branch_check = subprocess.run(
                ["git", "-C", str(worktree_path), "rev-parse", "--verify", branch_name],
                check=False,
                capture_output=True,
                timeout=30,
            )

            if branch_check.returncode == 0:
                subprocess.run(
                    ["git", "-C", str(worktree_path), "checkout", branch_name],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                logger.info("branch_checked_out", branch=branch_name)
                return

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree_path),
                    "checkout",
                    "-b",
                    branch_name,
                    f"origin/{base_branch}",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.info("branch_created", branch=branch_name)
        except subprocess.CalledProcessError as e:
            logger.error("branch_ensure_failed", error=str(e), stderr=e.stderr.decode())
            raise

    async def create_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        base_branch: str = "main",
    ) -> None:
        """Create a new branch in the worktree.

        Args:
            worktree_path: Path to the worktree
            branch_name: Name of the branch to create
            base_branch: Base branch to branch from (default: main)
        """
        logger.info("creating_branch", branch=branch_name, worktree=str(worktree_path))

        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree_path),
                    "checkout",
                    "-b",
                    branch_name,
                    f"origin/{base_branch}",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.info("branch_created", branch=branch_name)
        except subprocess.CalledProcessError as e:
            logger.error("branch_creation_failed", error=str(e), stderr=e.stderr.decode())
            raise

    async def commit_changes(
        self,
        worktree_path: Path,
        message: str,
        files: Optional[list[str]] = None,
    ) -> str:
        """Commit changes to the branch.

        Args:
            worktree_path: Path to the worktree
            message: Commit message
            files: Specific files to commit (if None, commits all staged changes)

        Returns:
            Commit hash
        """
        logger.info("committing_changes", message=message, worktree=str(worktree_path))

        try:
            if files:
                subprocess.run(
                    ["git", "-C", str(worktree_path), "add"] + files,
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            else:
                subprocess.run(
                    ["git", "-C", str(worktree_path), "add", "-A"],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

            result = subprocess.run(
                ["git", "-C", str(worktree_path), "commit", "-m", message],
                check=True,
                capture_output=True,
                timeout=60,
            )

            commit_hash = result.stdout.decode().split()[2]
            logger.info("changes_committed", commit_hash=commit_hash)
            return commit_hash
        except subprocess.CalledProcessError as e:
            logger.error("commit_failed", error=str(e), stderr=e.stderr.decode())
            raise

    async def push_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        force: bool = False,
    ) -> None:
        """Push the branch to remote.

        Args:
            worktree_path: Path to the worktree
            branch_name: Name of the branch to push
            force: Whether to force push (use with caution)
        """
        logger.info("pushing_branch", branch=branch_name, worktree=str(worktree_path))

        try:
            cmd = ["git", "-C", str(worktree_path), "push", "origin", branch_name]
            if force:
                cmd.insert(4, "-f")

            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=300,
            )
            logger.info("branch_pushed", branch=branch_name)
        except subprocess.CalledProcessError as e:
            logger.error("push_failed", error=str(e), stderr=e.stderr.decode())
            raise

    async def cleanup_worktree(self, worktree_path: Path) -> None:
        """Clean up a worktree after completion.

        Args:
            worktree_path: Path to the worktree to clean up
        """
        logger.info("cleaning_up_worktree", worktree=str(worktree_path))

        try:
            import shutil

            if worktree_path.exists():
                shutil.rmtree(worktree_path)
                logger.info("worktree_cleaned", worktree=str(worktree_path))
        except Exception as e:
            logger.error("cleanup_failed", error=str(e), worktree=str(worktree_path))
