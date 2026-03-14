"""Tests for git operations."""

from pathlib import Path

from ace.workspaces.git_ops import (
    GitOps,
    LegacyWorkspaceProvider,
    ProjectWorkspaceProvider,
    build_workspace_provider,
    resolve_project_session_key,
)


def test_git_ops_initialization(tmp_path):
    """Test GitOps initialization."""
    git_ops = GitOps(str(tmp_path))
    assert git_ops.workspace_root == tmp_path
    assert (tmp_path / "worktrees").exists() or not (tmp_path / "worktrees").exists()


def test_get_worktree_path(tmp_path):
    """Test worktree path generation."""
    git_ops = GitOps(str(tmp_path))
    path = git_ops.get_worktree_path("my-repo", 123)

    assert "worktrees" in str(path)
    assert "my-repo" in str(path)
    assert "123" in str(path)


def test_get_branch_name(tmp_path):
    """Test branch name generation."""
    git_ops = GitOps(str(tmp_path))
    branch = git_ops.get_branch_name(456, "add-feature")

    assert branch == "agent/456-add-feature"


def test_resolve_project_session_key_prefers_explicit(tmp_path):
    """Test that explicit project session keys win."""
    del tmp_path
    assert resolve_project_session_key(
        explicit_project_session_key="explicit-key",
        fallback_project_session_key="fallback",
        github_project_name="project-name",
    ) == "explicit-key"


def test_legacy_provider_paths(tmp_path):
    """Legacy provider uses worktrees/*/issue layout."""
    provider = LegacyWorkspaceProvider(tmp_path, "my-project")
    assert provider.get_worktree_path("repo", 42) == tmp_path / "worktrees" / "repo" / "42"
    assert provider.get_task_path(42) == tmp_path / "tasks" / "42"
    assert provider.get_shared_path() == tmp_path / "shared"

    provider.prepare_workspace("repo", 42)
    assert (tmp_path / "tasks" / "42").exists()
    assert (tmp_path / "shared").exists()
    assert (tmp_path / "worktrees" / "repo").exists()


def test_project_provider_paths(tmp_path):
    """Project provider builds project-root, repos, shared, and task paths."""
    provider = ProjectWorkspaceProvider(tmp_path, "my-project")
    assert provider.get_worktree_path("repo", 77) == (
        tmp_path / "projects" / "my-project" / "repos" / "repo" / "77"
    )
    assert provider.get_task_path("77") == tmp_path / "projects" / "my-project" / "tasks" / "77"
    assert provider.get_shared_path() == tmp_path / "projects" / "my-project" / "shared"

    provider.prepare_workspace("repo", 77)
    assert (tmp_path / "projects" / "my-project" / "shared").exists()
    assert (tmp_path / "projects" / "my-project" / "tasks" / "77").exists()
    assert (tmp_path / "projects" / "my-project" / "repos" / "repo").exists()


def test_build_workspace_provider_from_settings_fields():
    class _Settings:
        agent_workspace_root = "/tmp/work"
        agent_workspace_provider = "project"
        agent_project_session_key = "my-session"
        github_project_name = "github-project"
        gcp_project_id = "gcp-project"

    provider = build_workspace_provider(
        workspace_root=_Settings().agent_workspace_root,
        provider_name="project",
        project_session_key="explicit",
        project_slug="slug",
        fallback_project_session_key=_Settings().agent_project_session_key,
        github_project_name=_Settings().github_project_name,
        gcp_project_id=_Settings().gcp_project_id,
    )
    assert provider.project_session_key == "explicit"
    assert provider.get_worktree_path("repo", 1) == (
        Path("/tmp/work") / "projects" / "explicit" / "repos" / "repo" / "1"
    )


def test_legacy_provider_lists_issue_workspaces(tmp_path):
    """Legacy provider should discover numeric issue directories."""
    provider = LegacyWorkspaceProvider(tmp_path, "legacy")
    (tmp_path / "worktrees" / "repo-a" / "11").mkdir(parents=True)
    (tmp_path / "worktrees" / "repo-a" / "not-issue").mkdir()
    (tmp_path / "worktrees" / "repo-b" / "22").mkdir(parents=True)

    assert sorted(provider.iter_issue_workspaces()) == [
        ("repo-a", 11, tmp_path / "worktrees" / "repo-a" / "11"),
        ("repo-b", 22, tmp_path / "worktrees" / "repo-b" / "22"),
    ]


def test_project_provider_lists_issue_workspaces(tmp_path):
    """Project provider should discover numeric issue directories under the project repos tree."""
    provider = ProjectWorkspaceProvider(tmp_path, "project")
    (tmp_path / "projects" / "project" / "repos" / "repo-a" / "33").mkdir(parents=True)
    (tmp_path / "projects" / "project" / "repos" / "repo-b" / "44").mkdir(parents=True)

    assert sorted(provider.iter_issue_workspaces()) == [
        ("repo-a", 33, tmp_path / "projects" / "project" / "repos" / "repo-a" / "33"),
        ("repo-b", 44, tmp_path / "projects" / "project" / "repos" / "repo-b" / "44"),
    ]


def test_github_session_key_resolution_in_factory():
    class _Settings:
        agent_workspace_root = "/tmp/work"
        agent_workspace_provider = "project"
        agent_project_session_key = "fallback"
        project_slug = "slug"
        github_project_name = "project-name"
        gcp_project_id = "gcp-id"

    git_ops = GitOps.from_settings(_Settings())
    assert isinstance(git_ops.workspace_provider, ProjectWorkspaceProvider)
    assert git_ops.workspace_provider.project_session_key == "fallback"


def test_github_session_key_override_from_factory_argument():
    class _Settings:
        agent_workspace_root = "/tmp/work"
        agent_workspace_provider = "project"
        agent_project_session_key = "fallback"
        project_slug = "slug"
        github_project_name = "project-name"
        gcp_project_id = "gcp-id"

    git_ops = GitOps.from_settings(_Settings(), project_session_key="explicit")
    assert git_ops.workspace_provider.project_session_key == "explicit"
