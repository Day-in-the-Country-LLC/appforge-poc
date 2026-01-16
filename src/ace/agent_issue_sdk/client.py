"""Issue creation client for agents to create issues in DITC TODO project."""

from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class IssueContent:
    """Content for creating an issue."""

    title: str
    target_repository: str
    description: str
    acceptance_criteria: list[str]
    implementation_notes: Optional[str] = None
    related_issues: Optional[list[str]] = None


class IssueCreator:
    """Create issues in DITC TODO org project from any repo."""

    def __init__(
        self,
        github_token: str,
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
    ):
        """Initialize IssueCreator.

        Args:
            github_token: GitHub Personal Access Token
            github_org: GitHub organization (default: Day-in-the-Country-LLC)
            project_name: Project name in org (default: DITC TODO)
            api_url: GitHub API URL (default: https://api.github.com)
        """
        self.github_token = github_token
        self.github_org = github_org
        self.project_name = project_name
        self.api_url = api_url
        self.headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def create_issue(
        self,
        content: IssueContent,
        difficulty: str = "medium",
        labels: Optional[list[str]] = None,
    ) -> dict:
        """Create an issue in the DITC TODO project.

        Args:
            content: IssueContent with title, description, criteria, etc.
            difficulty: Difficulty level (easy, medium, hard)
            labels: Additional labels to add (agent label added automatically)

        Returns:
            Issue data from GitHub API

        Raises:
            ValueError: If difficulty is invalid
            httpx.HTTPError: If API call fails
        """
        if difficulty not in ("easy", "medium", "hard"):
            raise ValueError(f"Invalid difficulty: {difficulty}. Must be easy, medium, or hard.")

        logger.info(
            "creating_issue",
            title=content.title,
            repo=content.target_repository,
            difficulty=difficulty,
        )

        issue_body = self._format_issue_body(content)
        issue_labels = self._build_labels(difficulty, labels)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/repos/{self.github_org}/{self.github_org}/issues",
                    headers=self.headers,
                    json={
                        "title": content.title,
                        "body": issue_body,
                        "labels": issue_labels,
                    },
                )
                response.raise_for_status()

            issue_data = response.json()
            logger.info(
                "issue_created",
                issue_number=issue_data["number"],
                url=issue_data["html_url"],
            )
            return issue_data

        except httpx.HTTPError as e:
            logger.error("issue_creation_failed", error=str(e))
            raise

    def _format_issue_body(self, content: IssueContent) -> str:
        """Format issue body in standard format.

        Args:
            content: IssueContent

        Returns:
            Formatted issue body
        """
        body = f"""## Target Repository
{content.target_repository}

## Description
{content.description}

## Acceptance Criteria
"""
        for criterion in content.acceptance_criteria:
            body += f"- [ ] {criterion}\n"

        if content.implementation_notes:
            body += f"\n## Implementation Notes\n{content.implementation_notes}\n"

        if content.related_issues:
            body += "\n## Related Issues\n"
            for issue in content.related_issues:
                body += f"- {issue}\n"

        return body

    def _build_labels(self, difficulty: str, additional_labels: Optional[list[str]]) -> list[str]:
        """Build label list.

        Args:
            difficulty: Difficulty level
            additional_labels: Additional labels to include

        Returns:
            List of labels
        """
        labels = ["agent", f"difficulty:{difficulty}"]

        if additional_labels:
            labels.extend(additional_labels)

        return labels


class IssueCreatorSync:
    """Synchronous wrapper for IssueCreator (for non-async contexts)."""

    def __init__(
        self,
        github_token: str,
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
    ):
        """Initialize IssueCreatorSync.

        Args:
            github_token: GitHub Personal Access Token
            github_org: GitHub organization (default: Day-in-the-Country-LLC)
            project_name: Project name in org (default: DITC TODO)
            api_url: GitHub API URL (default: https://api.github.com)
        """
        self.client = IssueCreator(github_token, github_org, project_name, api_url)

    def create_issue(
        self,
        content: IssueContent,
        difficulty: str = "medium",
        labels: Optional[list[str]] = None,
    ) -> dict:
        """Create an issue in the DITC TODO project (synchronous).

        Args:
            content: IssueContent with title, description, criteria, etc.
            difficulty: Difficulty level (easy, medium, hard)
            labels: Additional labels to add (agent label added automatically)

        Returns:
            Issue data from GitHub API

        Raises:
            ValueError: If difficulty is invalid
            httpx.HTTPError: If API call fails
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.client.create_issue(content, difficulty, labels))
