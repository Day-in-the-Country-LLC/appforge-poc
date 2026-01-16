"""GitHub Projects V2 operations using GraphQL API."""

from dataclasses import dataclass

import structlog

from .api_client import GitHubAPIClient

logger = structlog.get_logger(__name__)


@dataclass
class BlockingIssue:
    """Represents an issue that blocks another issue."""

    number: int
    repo_owner: str
    repo_name: str
    state: str  # "OPEN" or "CLOSED"
    title: str


@dataclass
class ProjectItem:
    """Represents an item in a GitHub Project V2."""

    item_id: str
    content_id: str
    content_type: str  # "Issue" or "PullRequest"
    title: str
    number: int
    repo_owner: str
    repo_name: str
    status: str | None
    labels: list[str]
    html_url: str
    blocking_issues: list[BlockingIssue] | None = None


class ProjectsV2Client:
    """Client for GitHub Projects V2 GraphQL operations."""

    def __init__(self, api_client: GitHubAPIClient):
        """Initialize the Projects V2 client.

        Args:
            api_client: GitHub API client instance
        """
        self.api_client = api_client

    async def get_org_project_id(self, org: str, project_name: str) -> str | None:
        """Get the project ID for an organization project by name.

        Args:
            org: Organization name
            project_name: Project name

        Returns:
            Project ID or None if not found
        """
        query = """
        query($org: String!, $cursor: String) {
            organization(login: $org) {
                projectsV2(first: 20, after: $cursor) {
                    nodes {
                        id
                        title
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
        }
        """
        cursor = None
        while True:
            result = await self.api_client.graphql(query, {"org": org, "cursor": cursor})
            projects = result["organization"]["projectsV2"]
            for project in projects["nodes"]:
                if project["title"] == project_name:
                    logger.info("project_found", org=org, name=project_name, id=project["id"])
                    return project["id"]
            if not projects["pageInfo"]["hasNextPage"]:
                break
            cursor = projects["pageInfo"]["endCursor"]
        logger.warning("project_not_found", org=org, name=project_name)
        return None

    async def get_status_field_id(self, project_id: str) -> tuple[str, dict[str, str]] | None:
        """Get the Status field ID and option mappings for a project.

        Args:
            project_id: Project node ID

        Returns:
            Tuple of (field_id, {option_name: option_id}) or None
        """
        query = """
        query($projectId: ID!) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    fields(first: 20) {
                        nodes {
                            ... on ProjectV2SingleSelectField {
                                id
                                name
                                options {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        result = await self.api_client.graphql(query, {"projectId": project_id})
        fields = result["node"]["fields"]["nodes"]
        for field in fields:
            if field.get("name") == "Status":
                options = {opt["name"]: opt["id"] for opt in field.get("options", [])}
                logger.info(
                    "status_field_found", field_id=field["id"], options=list(options.keys())
                )
                return field["id"], options
        logger.warning("status_field_not_found", project_id=project_id)
        return None

    async def list_project_items_by_status(
        self,
        project_id: str,
        status: str,
    ) -> list[ProjectItem]:
        """List project items filtered by status.

        Args:
            project_id: Project node ID
            status: Status value to filter by (e.g., "Ready")

        Returns:
            List of ProjectItem objects matching the status
        """
        query = """
        query($projectId: ID!, $cursor: String) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    items(first: 50, after: $cursor) {
                        nodes {
                            id
                            fieldValueByName(name: "Status") {
                                ... on ProjectV2ItemFieldSingleSelectValue {
                                    name
                                }
                            }
                            content {
                                __typename
                                ... on Issue {
                                    id
                                    title
                                    number
                                    url
                                    labels(first: 20) {
                                        nodes {
                                            name
                                        }
                                    }
                                    repository {
                                        owner {
                                            login
                                        }
                                        name
                                    }
                                }
                                ... on PullRequest {
                                    id
                                    title
                                    number
                                    url
                                    labels(first: 20) {
                                        nodes {
                                            name
                                        }
                                    }
                                    repository {
                                        owner {
                                            login
                                        }
                                        name
                                    }
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        }
        """
        items: list[ProjectItem] = []
        cursor = None

        while True:
            result = await self.api_client.graphql(
                query, {"projectId": project_id, "cursor": cursor}
            )
            project_items = result["node"]["items"]

            for item in project_items["nodes"]:
                item_status = item.get("fieldValueByName", {})
                if item_status:
                    item_status = item_status.get("name")

                if item_status != status:
                    continue

                content = item.get("content")
                if not content:
                    continue

                content_type = content.get("__typename", "Issue")
                if content.get("number") is None:
                    continue

                items.append(
                    ProjectItem(
                        item_id=item["id"],
                        content_id=content["id"],
                        content_type=content_type,
                        title=content["title"],
                        number=content["number"],
                        repo_owner=content["repository"]["owner"]["login"],
                        repo_name=content["repository"]["name"],
                        status=item_status,
                        labels=[
                            label["name"] for label in content.get("labels", {}).get("nodes", [])
                        ],
                        html_url=content["url"],
                    )
                )

            if not project_items["pageInfo"]["hasNextPage"]:
                break
            cursor = project_items["pageInfo"]["endCursor"]

        logger.info("project_items_listed", status=status, count=len(items))
        return items

    async def update_item_status(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        option_id: str,
    ) -> None:
        """Update the status of a project item.

        Args:
            project_id: Project node ID
            item_id: Project item node ID
            field_id: Status field ID
            option_id: Status option ID
        """
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
            updateProjectV2ItemFieldValue(input: {
                projectId: $projectId
                itemId: $itemId
                fieldId: $fieldId
                value: $value
            }) {
                projectV2Item {
                    id
                }
            }
        }
        """
        await self.api_client.graphql(
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": field_id,
                "value": {"singleSelectOptionId": option_id},
            },
        )
        logger.info("item_status_updated", item_id=item_id)

    async def get_item_id_for_issue(
        self,
        project_id: str,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> str | None:
        """Find the project item ID for a specific issue.

        Args:
            project_id: Project node ID
            issue_number: Issue number
            repo_owner: Repository owner
            repo_name: Repository name

        Returns:
            Project item ID or None if not found
        """
        query = """
        query($projectId: ID!, $cursor: String) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    items(first: 100, after: $cursor) {
                        nodes {
                            id
                            content {
                                ... on Issue {
                                    number
                                    repository {
                                        owner {
                                            login
                                        }
                                        name
                                    }
                                }
                            }
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        }
        """
        cursor = None
        while True:
            result = await self.api_client.graphql(
                query, {"projectId": project_id, "cursor": cursor}
            )
            items = result["node"]["items"]

            for item in items["nodes"]:
                content = item.get("content")
                if not content or content.get("number") != issue_number:
                    continue
                repo = content.get("repository", {})
                if (
                    repo.get("owner", {}).get("login") == repo_owner
                    and repo.get("name") == repo_name
                ):
                    return item["id"]

            if not items["pageInfo"]["hasNextPage"]:
                break
            cursor = items["pageInfo"]["endCursor"]

        logger.warning(
            "item_not_found_in_project",
            issue_number=issue_number,
            repo=f"{repo_owner}/{repo_name}",
        )
        return None

    async def get_issue_blockers(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> list[BlockingIssue]:
        """Get issues that block this issue (trackedInIssues relationship).

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            issue_number: Issue number

        Returns:
            List of BlockingIssue objects representing issues that block this one
        """
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $number) {
                    trackedInIssues(first: 50) {
                        nodes {
                            number
                            title
                            state
                            repository {
                                owner {
                                    login
                                }
                                name
                            }
                        }
                    }
                }
            }
        }
        """
        try:
            result = await self.api_client.graphql(
                query,
                {"owner": repo_owner, "repo": repo_name, "number": issue_number},
            )
            tracked_in = result.get("repository", {}).get("issue", {})
            if not tracked_in:
                return []

            blockers = []
            for node in tracked_in.get("trackedInIssues", {}).get("nodes", []):
                blockers.append(
                    BlockingIssue(
                        number=node["number"],
                        title=node["title"],
                        state=node["state"],
                        repo_owner=node["repository"]["owner"]["login"],
                        repo_name=node["repository"]["name"],
                    )
                )

            logger.debug(
                "issue_blockers_fetched",
                issue=issue_number,
                blocker_count=len(blockers),
                open_blockers=sum(1 for b in blockers if b.state == "OPEN"),
            )
            return blockers

        except Exception as e:
            logger.warning(
                "get_issue_blockers_failed",
                issue=issue_number,
                error=str(e),
            )
            return []

    async def has_open_blockers(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> bool:
        """Check if an issue has any open blocking issues.

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            issue_number: Issue number

        Returns:
            True if issue has open blockers, False otherwise
        """
        blockers = await self.get_issue_blockers(repo_owner, repo_name, issue_number)
        open_blockers = [b for b in blockers if b.state == "OPEN"]
        if open_blockers:
            logger.info(
                "issue_has_open_blockers",
                issue=issue_number,
                open_blockers=[b.number for b in open_blockers],
            )
            return True
        return False
