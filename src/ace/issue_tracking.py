"""Factory helpers for issue tracker and enricher backends."""

from __future__ import annotations

from ace.config.settings import get_settings
from ace.config.secrets import resolve_github_token
from ace.github.api_client import GitHubAPIClient
from ace.github.work_items import (
    WorkItemEnricher,
    WorkItemTracker,
    build_github_work_item_enricher,
    build_github_work_item_tracker,
)
from ace.linear.work_items import LinearWorkItemTracker


def build_work_item_tracker(
    api_client: GitHubAPIClient | None = None,
    owner: str = "",
    repo: str = "",
) -> WorkItemTracker:
    """Build a tracker instance based on configured backend."""
    settings = get_settings()
    backend = (settings.issue_tracker_backend or "github").lower()

    if backend == "linear":
        return LinearWorkItemTracker.create_default()

    # Legacy/default path remains GitHub-based project tracker.
    if api_client is None:
        token = resolve_github_token(settings)
        api_client = GitHubAPIClient(token)
    return build_github_work_item_tracker(api_client, owner, repo)


def build_work_item_enricher(
    api_client: GitHubAPIClient | None = None,
    owner: str = "",
    repo: str = "",
) -> WorkItemEnricher:
    """Build a GitHub-only enricher for comments/comments and PR operations."""
    if api_client is None:
        token = resolve_github_token(get_settings())
        api_client = GitHubAPIClient(token)
    return build_github_work_item_enricher(api_client, owner, repo)
