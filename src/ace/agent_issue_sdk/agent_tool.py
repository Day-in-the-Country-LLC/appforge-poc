"""Agent tool wrapper for the Issue Creator SDK.

This module provides agent-friendly tool functions that wrap the SDK
so agents can create issues without writing scripts.
"""

import asyncio
import json
from typing import Optional

from ace.agent_issue_sdk.client import IssueCreator, IssueContent


async def create_github_issue(
    title: str,
    description: str,
    target_repository: str = "irlsc-events",
    acceptance_criteria: Optional[list[str]] = None,
    implementation_notes: Optional[str] = None,
    dependencies: Optional[list[str]] = None,
    blocks: Optional[list[str]] = None,
    difficulty: str = "medium",
    labels: Optional[list[str]] = None,
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Create a GitHub issue using the Agent Issue SDK.

    This is the main agent tool for creating issues. It returns MCP instructions
    that can be executed by the agent to create the issue in GitHub.

    Args:
        title: Issue title
        description: Issue description
        target_repository: GitHub repository name (default: irlsc-events)
        acceptance_criteria: List of acceptance criteria
        implementation_notes: Implementation notes for the issue
        dependencies: List of issue numbers this issue depends on (e.g., ["#193", "#194"])
        blocks: List of issue numbers this issue blocks (e.g., ["#195"])
        difficulty: Difficulty level (easy, medium, hard)
        labels: Additional labels to add to the issue
        github_org: GitHub organization (default: Day-in-the-Country-LLC)

    Returns:
        Dict with MCP server instructions for creating the issue.
        Structure:
        {
            "mcp_server": "github",
            "operation": "create_issue",
            "parameters": {
                "owner": str,
                "repo": str,
                "title": str,
                "body": str,
                "labels": list[str]
            },
            "post_actions": {
                "dependencies": list[str],
                "blocks": list[str]
            }
        }

    Example:
        >>> mcp_instructions = await create_github_issue(
        ...     title="Feature: Add caching",
        ...     description="Add Redis caching to improve performance",
        ...     acceptance_criteria=["Cache is working", "Tests pass"],
        ...     dependencies=["#193"],
        ...     blocks=["#195"],
        ...     difficulty="medium"
        ... )
        >>> # Agent would then use these MCP instructions with GitHub's MCP server
    """
    creator = IssueCreator(
        github_org=github_org,
        project_name="DITC TODO",
    )

    # Build acceptance criteria with defaults
    if acceptance_criteria is None:
        acceptance_criteria = []

    # Create the issue content
    issue_content = IssueContent(
        title=title,
        target_repository=target_repository,
        description=description,
        acceptance_criteria=acceptance_criteria,
        implementation_notes=implementation_notes,
        dependencies=dependencies,
        blocks=blocks,
    )

    # Get MCP instructions from the SDK
    mcp_instructions = await creator.create_issue(
        issue_content,
        difficulty=difficulty,
        labels=labels,
    )

    return mcp_instructions


async def update_github_issue(
    issue_number: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    state: Optional[str] = None,
    labels: Optional[list[str]] = None,
    target_repository: str = "irlsc-events",
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Update a GitHub issue.

    Args:
        issue_number: Issue number to update
        title: New title (optional)
        description: New description/body (optional)
        state: New state - "open" or "closed" (optional)
        labels: New labels list (optional)
        target_repository: GitHub repository name (default: irlsc-events)
        github_org: GitHub organization (default: Day-in-the-Country-LLC)

    Returns:
        Dict with MCP server instructions for updating the issue.
        Structure:
        {
            "mcp_server": "github",
            "operation": "update_issue",
            "parameters": {
                "owner": str,
                "repo": str,
                "issue_number": int,
                "updates": {
                    "title": str (optional),
                    "body": str (optional),
                    "state": str (optional),
                    "labels": list[str] (optional)
                }
            }
        }

    Example:
        >>> mcp_instructions = await update_github_issue(
        ...     issue_number=199,
        ...     title="Updated Title",
        ...     state="closed"
        ... )
    """
    updates = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["body"] = description
    if state is not None:
        updates["state"] = state
    if labels is not None:
        updates["labels"] = labels

    mcp_instructions = {
        "mcp_server": "github",
        "operation": "update_issue",
        "parameters": {
            "owner": github_org,
            "repo": target_repository,
            "issue_number": issue_number,
            "updates": updates,
        },
    }

    return mcp_instructions


async def delete_github_issue(
    issue_number: int,
    target_repository: str = "irlsc-events",
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Delete (close) a GitHub issue.

    Note: GitHub API doesn't truly delete issues, it closes them.
    This function closes the issue.

    Args:
        issue_number: Issue number to close
        target_repository: GitHub repository name (default: irlsc-events)
        github_org: GitHub organization (default: Day-in-the-Country-LLC)

    Returns:
        Dict with MCP server instructions for closing the issue.
        Structure:
        {
            "mcp_server": "github",
            "operation": "close_issue",
            "parameters": {
                "owner": str,
                "repo": str,
                "issue_number": int
            }
        }

    Example:
        >>> mcp_instructions = await delete_github_issue(issue_number=199)
    """
    mcp_instructions = {
        "mcp_server": "github",
        "operation": "close_issue",
        "parameters": {
            "owner": github_org,
            "repo": target_repository,
            "issue_number": issue_number,
        },
    }

    return mcp_instructions


def format_mcp_instructions(mcp_instructions: dict) -> str:
    """Format MCP instructions for display.

    Args:
        mcp_instructions: MCP instructions dict from create_github_issue, update_github_issue, or delete_github_issue

    Returns:
        Formatted string representation of the instructions
    """
    return json.dumps(mcp_instructions, indent=2)


# Synchronous wrappers for use in non-async contexts
def create_github_issue_sync(
    title: str,
    description: str,
    target_repository: str = "irlsc-events",
    acceptance_criteria: Optional[list[str]] = None,
    implementation_notes: Optional[str] = None,
    dependencies: Optional[list[str]] = None,
    blocks: Optional[list[str]] = None,
    difficulty: str = "medium",
    labels: Optional[list[str]] = None,
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Synchronous wrapper for create_github_issue.

    Use this if you're in a non-async context.

    Args:
        Same as create_github_issue

    Returns:
        Same as create_github_issue
    """
    return asyncio.run(
        create_github_issue(
            title=title,
            description=description,
            target_repository=target_repository,
            acceptance_criteria=acceptance_criteria,
            implementation_notes=implementation_notes,
            dependencies=dependencies,
            blocks=blocks,
            difficulty=difficulty,
            labels=labels,
            github_org=github_org,
        )
    )


def update_github_issue_sync(
    issue_number: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    state: Optional[str] = None,
    labels: Optional[list[str]] = None,
    target_repository: str = "irlsc-events",
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Synchronous wrapper for update_github_issue.

    Use this if you're in a non-async context.

    Args:
        Same as update_github_issue

    Returns:
        Same as update_github_issue
    """
    return asyncio.run(
        update_github_issue(
            issue_number=issue_number,
            title=title,
            description=description,
            state=state,
            labels=labels,
            target_repository=target_repository,
            github_org=github_org,
        )
    )


def delete_github_issue_sync(
    issue_number: int,
    target_repository: str = "irlsc-events",
    github_org: str = "Day-in-the-Country-LLC",
) -> dict:
    """Synchronous wrapper for delete_github_issue.

    Use this if you're in a non-async context.

    Args:
        Same as delete_github_issue

    Returns:
        Same as delete_github_issue
    """
    return asyncio.run(
        delete_github_issue(
            issue_number=issue_number,
            target_repository=target_repository,
            github_org=github_org,
        )
    )
