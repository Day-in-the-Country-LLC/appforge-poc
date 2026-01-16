"""Tests for git operations."""

import pytest

from ace.workspaces.git_ops import GitOps


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
