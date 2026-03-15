"""Bridge adapters for PR review actions against GitHub API operations."""

from __future__ import annotations

from typing import Any

from ace.github.issue_queue import IssueQueue


class PRReviewBridgeError(ValueError):
    """Domain error for PR review bridge failures."""


class PRReviewGitHubBridge:
    """Thin adapter over IssueQueue methods for PR review operations."""

    def __init__(self, issue_queue: IssueQueue) -> None:
        self.issue_queue = issue_queue

    async def submit_pull_request_review(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        event: str,
        body: str,
    ) -> dict[str, Any]:
        return await self.issue_queue.submit_pull_request_review(
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
            event=event,
            body=body,
        )

    async def merge_pull_request(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        sha: str,
        merge_method: str = "squash",
    ) -> dict[str, Any]:
        return await self.issue_queue.merge_pull_request(
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
            sha=sha,
            merge_method=merge_method,
        )

    async def get_pull_request_status(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> dict[str, Any]:
        return await self.issue_queue.get_pull_request_status(
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
        )

    async def get_combined_status_for_ref(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        ref: str,
    ) -> str:
        return await self.issue_queue.get_combined_status_for_ref(
            repo_owner=repo_owner,
            repo_name=repo_name,
            ref=ref,
        )

    async def post_issue_comment(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        return await self.issue_queue.post_comment(
            issue_number=issue_number,
            body=body,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
