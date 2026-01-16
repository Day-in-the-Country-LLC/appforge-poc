"""Issue creation client for agents to create issues in DITC TODO project using GitHub MCP server."""

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog
from google.cloud import secretmanager

logger = structlog.get_logger(__name__)

# Note: This SDK is designed to work with GitHub's MCP server
# The MCP server handles all GitHub API interactions


@dataclass
class IssueContent:
    """Content for creating an issue."""

    title: str
    target_repository: str
    description: str
    acceptance_criteria: list[str]
    implementation_notes: Optional[str] = None
    related_issues: Optional[list[str]] = None
    dependencies: Optional[list[str]] = None
    blocks: Optional[list[str]] = None


class IssueCreator:
    """Create issues in DITC TODO org project from any repo."""

    def __init__(
        self,
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
        credentials_file: Optional[str] = None,
        secret_name: str = "github-control-api-key",
    ):
        """Initialize IssueCreator.

        Fetches GitHub token from GCP Secret Manager using credentials file.

        Args:
            github_org: GitHub organization (default: Day-in-the-Country-LLC)
            project_name: Project name in org (default: DITC TODO)
            api_url: GitHub API URL (default: https://api.github.com)
            credentials_file: Path to GCP credentials JSON file (default: appforge-creds.json)
            secret_name: Secret name in Secret Manager (default: github-control-api-key)
        """
        self.github_org = github_org
        self.project_name = project_name
        self.api_url = api_url

        credentials_file = credentials_file or self._find_credentials_file()
        gcp_project_id = self._get_project_id_from_credentials(credentials_file)

        self.github_token = self._fetch_secret(
            gcp_project_id, secret_name, credentials_file
        )
        self.headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

    @staticmethod
    def _find_credentials_file() -> str:
        """Auto-detect credentials file matching *-creds.json pattern.

        Returns:
            Path to credentials file

        Raises:
            FileNotFoundError: If no credentials file found
        """
        pattern = "*-creds.json"
        matches = glob.glob(pattern)
        if not matches:
            raise FileNotFoundError(
                f"No credentials file found matching pattern '{pattern}'. "
                "Ensure a file like 'belleandsuds-creds.json' exists in the repo root."
            )
        if len(matches) > 1:
            logger.warning(
                "multiple_credentials_files_found", files=matches, using=matches[0]
            )
        return matches[0]

    @staticmethod
    def _get_project_id_from_credentials(credentials_file: str) -> str:
        """Extract GCP project ID from credentials file.

        Args:
            credentials_file: Path to GCP credentials JSON file

        Returns:
            GCP project ID

        Raises:
            FileNotFoundError: If credentials file not found
            ValueError: If project_id not in credentials file
        """
        creds_path = Path(credentials_file)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {credentials_file}. "
                "Ensure appforge-creds.json exists in the repo root."
            )

        try:
            with open(creds_path) as f:
                creds = json.load(f)
            project_id = creds.get("project_id")
            if not project_id:
                raise ValueError("project_id not found in credentials file")
            return project_id
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in credentials file: {e}")

    @staticmethod
    def _fetch_secret(project_id: str, secret_name: str, credentials_file: str) -> str:
        """Fetch secret from GCP Secret Manager using credentials file.

        Args:
            project_id: GCP project ID
            secret_name: Secret name
            credentials_file: Path to GCP credentials JSON file

        Returns:
            Secret value

        Raises:
            Exception: If secret fetch fails
        """
        try:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8").strip()
        except Exception as e:
            logger.error("secret_fetch_failed", secret=secret_name, error=str(e))
            raise

    async def create_issue(
        self,
        content: IssueContent,
        difficulty: str = "medium",
        labels: Optional[list[str]] = None,
    ) -> dict:
        """Create an issue in the DITC TODO project using GitHub MCP server.

        This method returns instructions for the agent to use the GitHub MCP server
        to create the issue. The agent will handle the actual GitHub operations.

        Args:
            content: IssueContent with title, description, criteria, etc.
            difficulty: Difficulty level (easy, medium, hard)
            labels: Additional labels to add (agent label added automatically)

        Returns:
            Dict with MCP server instructions for creating the issue

        Raises:
            ValueError: If difficulty is invalid
        """
        if difficulty not in ("easy", "medium", "hard"):
            raise ValueError(
                f"Invalid difficulty: {difficulty}. Must be easy, medium, or hard."
            )

        logger.info(
            "creating_issue",
            title=content.title,
            repo=content.target_repository,
            difficulty=difficulty,
        )

        issue_body = self._format_issue_body(content)
        issue_labels = self._build_labels(difficulty, labels)

        # Return MCP server instructions for the agent to execute
        mcp_instructions = {
            "mcp_server": "github",
            "operation": "create_issue",
            "parameters": {
                "owner": self.github_org,
                "repo": content.target_repository,
                "title": content.title,
                "body": issue_body,
                "labels": issue_labels,
            },
            "post_actions": {
                "dependencies": content.dependencies or [],
                "blocks": content.blocks or [],
                "add_to_project": True,
            },
        }

        logger.info(
            "issue_creation_delegated_to_mcp",
            repo=content.target_repository,
            title=content.title,
        )

        return mcp_instructions

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

    def _build_labels(
        self, difficulty: str, additional_labels: Optional[list[str]]
    ) -> list[str]:
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
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
        credentials_file: Optional[str] = None,
        secret_name: str = "github-control-api-key",
    ):
        """Initialize IssueCreatorSync.

        Fetches GitHub token from GCP Secret Manager using credentials file.

        Args:
            github_org: GitHub organization (default: Day-in-the-Country-LLC)
            project_name: Project name in org (default: DITC TODO)
            api_url: GitHub API URL (default: https://api.github.com)
            credentials_file: Path to GCP credentials JSON file (default: appforge-creds.json)
            secret_name: Secret name in Secret Manager (default: github-control-api-key)
        """
        self.client = IssueCreator(
            github_org, project_name, api_url, credentials_file, secret_name
        )

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
        return loop.run_until_complete(
            self.client.create_issue(content, difficulty, labels)
        )
