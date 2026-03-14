"""Git operations for workspace management."""

import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import structlog

logger = structlog.get_logger(__name__)

CLONE_TIMEOUT_SECONDS = 900
CLONE_DEPTH = 1
CLONE_FILTER = "blob:none"


def _normalize_segment(value: str) -> str:
    """Normalize a string so it is safe to use as a workspace directory segment."""
    safe = value.strip().replace(os.path.sep, "-")
    return (
        "".join(char if char.isalnum() or char in {"_", "-", "."} else "-" for char in safe).strip(
            "-."
        )
        or "default"
    )


class WorkspaceProvider(ABC):
    """Interface for workspace path resolution."""

    def __init__(self, workspace_root: str | Path, project_session_key: str):
        self.workspace_root = Path(workspace_root)
        self.project_session_key = _normalize_segment(project_session_key)

    @abstractmethod
    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        """Return the path for a repository-specific issue workspace."""

    @abstractmethod
    def get_task_path(self, task_id: str | int) -> Path:
        """Return the per-task directory path."""

    @abstractmethod
    def get_shared_path(self) -> Path:
        """Return the shared project-level directory path."""

    @abstractmethod
    def iter_issue_workspaces(self) -> list[tuple[str, int, Path]]:
        """List known issue workspaces as (repo_name, issue_number, path)."""

    @abstractmethod
    def prepare_workspace(self, repo_name: str, issue_number: int) -> None:
        """Create directories required for project/task execution."""


class LegacyWorkspaceProvider(WorkspaceProvider):
    """Legacy layout provider: ``<root>/worktrees/<repo>/<issue>``."""

    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        return self.workspace_root / "worktrees" / repo_name / str(issue_number)

    def get_task_path(self, task_id: str | int) -> Path:
        return self.workspace_root / "tasks" / str(task_id)

    def get_shared_path(self) -> Path:
        return self.workspace_root / "shared"

    def iter_issue_workspaces(self) -> list[tuple[str, int, Path]]:
        worktrees_root = self.workspace_root / "worktrees"
        if not worktrees_root.exists():
            return []
        entries: list[tuple[str, int, Path]] = []
        for repo_dir in worktrees_root.iterdir():
            if not repo_dir.is_dir():
                continue
            for issue_dir in repo_dir.iterdir():
                if not issue_dir.is_dir() or not issue_dir.name.isdigit():
                    continue
                entries.append((repo_dir.name, int(issue_dir.name), issue_dir))
        return entries

    def prepare_workspace(self, repo_name: str, issue_number: int) -> None:  # noqa: ARG002
        self.get_shared_path().mkdir(parents=True, exist_ok=True)
        self.get_task_path(issue_number).mkdir(parents=True, exist_ok=True)
        self.get_worktree_path(repo_name, issue_number).parent.mkdir(parents=True, exist_ok=True)


class ProjectWorkspaceProvider(WorkspaceProvider):
    """Project-root layout provider with workspace state and task directories."""

    def _project_root(self) -> Path:
        return self.workspace_root / "projects" / self.project_session_key

    def _repos_root(self) -> Path:
        return self._project_root() / "repos"

    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        return self._repos_root() / repo_name / str(issue_number)

    def get_task_path(self, task_id: str | int) -> Path:
        return self._project_root() / "tasks" / str(task_id)

    def get_shared_path(self) -> Path:
        return self._project_root() / "shared"

    def iter_issue_workspaces(self) -> list[tuple[str, int, Path]]:
        repos_root = self._repos_root()
        if not repos_root.exists():
            return []
        entries: list[tuple[str, int, Path]] = []
        for repo_dir in repos_root.iterdir():
            if not repo_dir.is_dir():
                continue
            for issue_dir in repo_dir.iterdir():
                if not issue_dir.is_dir() or not issue_dir.name.isdigit():
                    continue
                entries.append((repo_dir.name, int(issue_dir.name), issue_dir))
        return entries

    def prepare_workspace(self, repo_name: str, issue_number: int) -> None:
        self.get_shared_path().mkdir(parents=True, exist_ok=True)
        self.get_task_path(issue_number).mkdir(parents=True, exist_ok=True)
        self._repos_root().mkdir(parents=True, exist_ok=True)
        self.get_worktree_path(repo_name, issue_number).parent.mkdir(parents=True, exist_ok=True)


def resolve_project_session_key(
    *,
    explicit_project_session_key: str | None,
    project_slug: str | None = None,
    fallback_project_session_key: str | None = None,
    github_project_name: str | None = None,
    gcp_project_id: str | None = None,
) -> str:
    """Resolve the project session key from configured values."""
    candidates = [
        explicit_project_session_key,
        fallback_project_session_key,
        project_slug,
        github_project_name,
        gcp_project_id,
    ]
    for candidate in candidates:
        if candidate and str(candidate).strip():
            return _normalize_segment(str(candidate))
    return "default"


def build_workspace_provider(
    workspace_root: str | Path,
    provider_name: str = "legacy",
    *,
    project_session_key: str | None = None,
    project_slug: str | None = None,
    fallback_project_session_key: str | None = None,
    github_project_name: str | None = None,
    gcp_project_id: str | None = None,
) -> WorkspaceProvider:
    """Build the active workspace provider from configuration values."""
    mode = (provider_name or "legacy").strip().lower()
    key = resolve_project_session_key(
        explicit_project_session_key=project_session_key,
        project_slug=project_slug,
        fallback_project_session_key=fallback_project_session_key,
        github_project_name=github_project_name,
        gcp_project_id=gcp_project_id,
    )

    if mode in {"legacy", "legacy-workspace", "legacy_workspace"}:
        return LegacyWorkspaceProvider(workspace_root, key)
    if mode in {"project", "project-root", "project_root"}:
        return ProjectWorkspaceProvider(workspace_root, key)

    raise ValueError(f"❌ ERROR: invalid workspace provider '{provider_name}'")


class GitOps:
    """Manages git operations for agent workspaces."""

    def __init__(
        self,
        workspace_root: str,
        workspace_provider: WorkspaceProvider | None = None,
        project_session_key: str | None = None,
    ):
        """Initialize git operations.

        Args:
            workspace_root: Root directory for all workspaces
        """
        provider = workspace_provider
        if provider is None:
            provider = build_workspace_provider(
                workspace_root=workspace_root,
                provider_name="legacy",
                project_session_key=project_session_key,
            )
        self.workspace_root = provider.workspace_root
        self.workspace_provider = provider
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(
        cls,
        settings: object,
        project_session_key: str | None = None,
    ) -> "GitOps":
        """Create ``GitOps`` with provider configuration from settings."""
        return cls(
            workspace_root=getattr(settings, "agent_workspace_root", ""),
            workspace_provider=build_workspace_provider(
                workspace_root=getattr(settings, "agent_workspace_root", ""),
                provider_name=getattr(settings, "agent_workspace_provider", "legacy"),
                project_session_key=project_session_key,
                project_slug=getattr(settings, "project_slug", None),
                fallback_project_session_key=getattr(settings, "agent_project_session_key", None),
                github_project_name=getattr(settings, "github_project_name", ""),
                gcp_project_id=getattr(settings, "gcp_project_id", ""),
            ),
        )

    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        """Get the worktree path for an issue.

        Args:
            repo_name: Repository name
            issue_number: GitHub issue number

        Returns:
            Path to the worktree
        """
        return self.workspace_provider.get_worktree_path(repo_name, issue_number)

    def get_task_path(self, task_id: str | int) -> Path:
        """Get task directory for an issue/task ID."""
        return self.workspace_provider.get_task_path(task_id)

    def get_shared_path(self) -> Path:
        """Get shared project-level path for the active workspace provider."""
        return self.workspace_provider.get_shared_path()

    def list_issue_workspaces(self) -> list[tuple[str, int, Path]]:
        """List active issue workspaces."""
        return self.workspace_provider.iter_issue_workspaces()

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

    def _resolve_default_branch(self, worktree_path: Path) -> str:
        """Resolve the remote default branch from origin/HEAD."""
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "symbolic-ref",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error(
                "❌ ERROR: default_branch_resolve_failed",
                returncode=result.returncode,
                stderr=result.stderr.decode() if result.stderr else "",
            )
            raise RuntimeError("❌ ERROR: Unable to resolve default branch from origin/HEAD")

        ref = result.stdout.decode().strip()
        if not ref:
            logger.error("❌ ERROR: default_branch_resolve_empty")
            raise RuntimeError("❌ ERROR: Unable to resolve default branch from origin/HEAD")

        if ref.startswith("origin/"):
            return ref.split("/", 1)[1]

        return ref

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

        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        clone_cmd = [
            "git",
            "clone",
            "--filter",
            CLONE_FILTER,
            "--depth",
            str(CLONE_DEPTH),
            repo_url,
            str(worktree_path),
        ]

        try:
            subprocess.run(
                clone_cmd,
                check=True,
                capture_output=True,
                timeout=CLONE_TIMEOUT_SECONDS,
                env=env,
            )
            logger.info("repo_cloned", worktree_path=str(worktree_path))
            return worktree_path
        except subprocess.TimeoutExpired as e:
            logger.error(
                "❌ ERROR: clone_timed_out",
                timeout_seconds=CLONE_TIMEOUT_SECONDS,
                stderr=e.stderr.decode() if e.stderr else "",
            )
            raise
        except subprocess.CalledProcessError as e:
            logger.error(
                "❌ ERROR: clone_failed",
                returncode=e.returncode,
                stderr=e.stderr.decode() if e.stderr else "",
            )
            raise

    async def ensure_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        base_branch: str | None = None,
    ) -> None:
        """Ensure the branch exists and is checked out in the worktree.

        Args:
            worktree_path: Path to the worktree
            branch_name: Name of the branch to create or checkout
            base_branch: Base branch to branch from (default: main)
        """
        try:
            subprocess.run(
                ["git", "-C", str(worktree_path), "fetch", "origin", "--prune"],
                check=True,
                capture_output=True,
                timeout=120,
            )

            resolved_base_branch = base_branch or self._resolve_default_branch(worktree_path)
            logger.info(
                "ensuring_branch",
                branch=branch_name,
                base_branch=resolved_base_branch,
                worktree=str(worktree_path),
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
                    f"origin/{resolved_base_branch}",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.info("branch_created", branch=branch_name)
        except subprocess.CalledProcessError as e:
            logger.error(
                "❌ ERROR: branch_ensure_failed",
                error=str(e),
                stderr=e.stderr.decode() if e.stderr else "",
            )
            raise

    async def create_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        base_branch: str | None = None,
    ) -> None:
        """Create a new branch in the worktree.

        Args:
            worktree_path: Path to the worktree
            branch_name: Name of the branch to create
            base_branch: Base branch to branch from (default: main)
        """
        logger.info("creating_branch", branch=branch_name, worktree=str(worktree_path))

        try:
            resolved_base_branch = base_branch or self._resolve_default_branch(worktree_path)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree_path),
                    "checkout",
                    "-b",
                    branch_name,
                    f"origin/{resolved_base_branch}",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.info("branch_created", branch=branch_name)
        except subprocess.CalledProcessError as e:
            logger.error(
                "❌ ERROR: branch_creation_failed",
                error=str(e),
                stderr=e.stderr.decode() if e.stderr else "",
            )
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
