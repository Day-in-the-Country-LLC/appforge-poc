"""Agent Issue SDK - Create issues in DITC TODO project from any repo."""

from ace.agent_issue_sdk.client import IssueCreator, IssueContent
from ace.agent_issue_sdk.agent_tool import (
    create_github_issue,
    create_github_issue_sync,
    update_github_issue,
    update_github_issue_sync,
    delete_github_issue,
    delete_github_issue_sync,
    format_mcp_instructions,
)

__version__ = "0.1.0"
__all__ = [
    "IssueCreator",
    "IssueContent",
    "create_github_issue",
    "create_github_issue_sync",
    "update_github_issue",
    "update_github_issue_sync",
    "delete_github_issue",
    "delete_github_issue_sync",
    "format_mcp_instructions",
]
