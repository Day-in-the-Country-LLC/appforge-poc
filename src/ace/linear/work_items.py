"""Linear-backed work item tracker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from ace.config.secrets import resolve_linear_api_key
from ace.config.settings import get_settings
from ace.github.issue_queue import Issue
from ace.github.work_items import BlockingIssue, WorkBoardItem, WorkItemTracker

from .api_client import LinearAPIClient

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _LinearIssueRef:
    id: str
    number: int
    title: str
    repo_owner: str | None
    repo_name: str | None
    status: str
    html_url: str


class LinearWorkItemTracker(WorkItemTracker):
    """Linear-specific implementation of work-item tracker operations."""

    def __init__(
        self,
        api_client: LinearAPIClient | None = None,
        *,
        default_project_name: str = "",
        default_repo_owner: str = "",
        default_repo_name: str = "",
        team_name: str = "",
    ) -> None:
        self.api_client = api_client
        self.default_project_name = default_project_name
        self.default_repo_owner = default_repo_owner
        self.default_repo_name = default_repo_name
        self.team_name = team_name
        self._project_id_cache: dict[str, str] = {}
        self._project_states_cache: dict[str, dict[str, str]] = {}

    @classmethod
    def create_default(cls) -> "LinearWorkItemTracker":
        settings = get_settings()
        api_key = resolve_linear_api_key(settings)
        client = LinearAPIClient(
            api_key,
            api_url=settings.linear_api_url,
        )
        return cls(
            client,
            default_project_name=settings.linear_default_project_name,
            default_repo_owner=settings.linear_default_repo_owner,
            default_repo_name=settings.linear_default_repo_name,
            team_name=settings.linear_team_name,
        )

    async def list_issues_by_project_status(
        self,
        project_name: str,
        status: str,
    ) -> list[Issue]:
        project_id = await self.get_project_id(project_name or self.default_project_name)
        if not project_id:
            return []

        query = """
        query($teamId: ID!, $cursor: String) {
          team(id: $teamId) {
            issues(first: 100, after: $cursor) {
              nodes {
                id
                number
                title
                url
                state {
                  name
                }
                labels {
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
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        normalized_status = _normalize_status(status)
        issues: list[Issue] = []
        cursor: str | None = None
        while True:
            result = await self.api_client.graphql(
                query,
                {"teamId": project_id, "cursor": cursor},
            )
            issue_nodes = (
                (((result or {}).get("team") or {}).get("issues") or {}).get("nodes") or []
            )
            for node in issue_nodes:
                if not isinstance(node, dict):
                    continue
                state = _normalize_status(_extract_state_name(node))
                if state != normalized_status:
                    continue

                issue_ref = _to_issue_ref(
                    node,
                    fallback_repo_owner=self.default_repo_owner,
                    fallback_repo_name=self.default_repo_name,
                )
                issue = _issue_from_ref(issue_ref)
                labels: list[str] = [
                    item.get("name", "")
                    for item in (node.get("labels") or {}).get("nodes", [])
                    if isinstance(item, dict) and item.get("name")
                ]
                issue.labels = labels
                issues.append(issue)

            page_info = (((result or {}).get("team") or {}).get("issues") or {}).get(
                "pageInfo"
            )
            if not page_info or not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        logger.info(
            "linear_issues_listed_by_status",
            project=project_name or self.default_project_name,
            status=status,
            count=len(issues),
        )
        return issues

    async def get_issue(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> Issue:
        issue_ref = await self._find_issue(issue_number, repo_owner, repo_name)
        if issue_ref is None:
            raise ValueError(f"❌ ERROR: Linear issue not found: {repo_owner}/{repo_name}#{issue_number}")
        return _issue_from_ref(issue_ref)

    async def get_project_id(self, project_name: str) -> str | None:
        normalized = _normalize_status(project_name)
        if not normalized:
            return None
        if normalized in self._project_id_cache:
            return self._project_id_cache[normalized]

        project_id = await self._find_team_id(normalized)
        if project_id:
            self._project_id_cache[normalized] = project_id
            return project_id
        logger.warning("linear_project_not_found", project_name=project_name)
        return None

    async def get_project_item_by_id(self, item_id: str) -> WorkBoardItem | None:
        query = """
        query($itemId: ID!) {
          issue(id: $itemId) {
            id
            number
            title
            url
            state {
              name
            }
            labels {
              nodes {
                name
              }
            }
          }
        }
        """
        result = await self.api_client.graphql(query, {"itemId": item_id})
        node = (result or {}).get("issue")
        if not isinstance(node, dict):
            return None
        issue_ref = _to_issue_ref(
            node,
            fallback_repo_owner=self.default_repo_owner,
            fallback_repo_name=self.default_repo_name,
        )
        return WorkBoardItem(
            item_id=issue_ref.id,
            content_id=issue_ref.id,
            content_type="Issue",
            title=issue_ref.title,
            number=issue_ref.number,
            repo_owner=issue_ref.repo_owner,
            repo_name=issue_ref.repo_name,
            status=issue_ref.status,
            labels=[
                item.get("name", "")
                for item in (node.get("labels") or {}).get("nodes", [])
                if isinstance(item, dict) and item.get("name")
            ],
            html_url=issue_ref.html_url,
        )

    async def get_item_id_for_issue(
        self,
        project_id: str,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> str | None:
        issue = await self._find_issue(
            issue_number,
            repo_owner,
            repo_name,
            project_id=project_id,
        )
        if issue is None:
            return None
        return issue.id

    async def get_issue_project_status(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
        project_name: str,
    ) -> str | None:
        issue = await self._find_issue(
            issue_number,
            repo_owner,
            repo_name,
            project_name=project_name,
        )
        return issue.status if issue else None

    async def set_issue_project_status(
        self,
        issue_number: int,
        status: str,
        project_name: str,
        *,
        repo_owner: str,
        repo_name: str,
    ) -> None:
        issue_ref = await self._find_issue(
            issue_number,
            repo_owner,
            repo_name,
            project_name=project_name,
        )
        if issue_ref is None:
            raise ValueError(
                f"❌ ERROR: Linear issue not found: {repo_owner}/{repo_name}#{issue_number}"
            )

        project_id = await self.get_project_id(project_name)
        if not project_id:
            raise ValueError(f"❌ ERROR: Linear project '{project_name}' not found")

        state_id = await self._get_state_id(project_id, status)
        if not state_id:
            raise ValueError(f"❌ ERROR: Linear state '{status}' not found")

        mutation = """
        mutation($input: IssueUpdateInput!) {
          issueUpdate(input: $input) {
            success
          }
        }
        """
        await self.api_client.graphql(
            mutation,
            {
                "input": {
                    "id": issue_ref.id,
                    "stateId": state_id,
                }
            },
        )
        logger.info(
            "linear_issue_status_updated",
            issue=issue_number,
            repo=f"{repo_owner}/{repo_name}",
            status=status,
        )

    async def get_issue_blockers(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> list[BlockingIssue]:
        issue_ref = await self._find_issue(
            issue_number,
            repo_owner,
            repo_name,
        )
        if issue_ref is None:
            return []

        query = """
        query($issueId: ID!) {
          issue(id: $issueId) {
            blockedByIssues(first: 100) {
              nodes {
                number
                title
                url
                state {
                  name
                }
              }
            }
            dependencies(first: 100) {
              nodes {
                number
                title
                url
                state {
                  name
                }
              }
            }
          }
        }
        """
        result = await self.api_client.graphql(query, {"issueId": issue_ref.id})
        issue = (result or {}).get("issue") or {}
        blockers: list[BlockingIssue] = []

        for node in _extract_blocker_nodes(issue):
            if not isinstance(node, dict):
                continue
            number = _safe_int(node.get("number"))
            if number is None:
                continue
            blockers.append(
                BlockingIssue(
                    number=number,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    state=_extract_state_name(node),
                    title=node.get("title") or "",
                )
            )
        return blockers

    async def close(self) -> None:
        if self.api_client is not None:
            await self.api_client.close()

    async def _find_issue(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
        project_name: str | None = None,
        project_id: str | None = None,
    ) -> _LinearIssueRef | None:
        resolved_project = project_id or await self.get_project_id(project_name or self.default_project_name)
        if not resolved_project:
            return None

        query = """
        query($teamId: ID!, $cursor: String) {
          team(id: $teamId) {
            issues(first: 100, after: $cursor) {
              nodes {
                id
                number
                title
                url
                state {
                  name
                }
                repository {
                  owner {
                    login
                  }
                  name
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        cursor: str | None = None
        while True:
            result = await self.api_client.graphql(
                query,
                {"teamId": resolved_project, "cursor": cursor},
            )
            issue_nodes = (
                (((result or {}).get("team") or {}).get("issues") or {}).get("nodes") or []
            )
            for node in issue_nodes:
                if not isinstance(node, dict):
                    continue
                issue_ref = _to_issue_ref(
                    node,
                    fallback_repo_owner=self.default_repo_owner,
                    fallback_repo_name=self.default_repo_name,
                )
                if issue_ref.number != issue_number:
                    continue
                if repo_owner and issue_ref.repo_owner != repo_owner:
                    continue
                if repo_name and issue_ref.repo_name != repo_name:
                    continue
                return issue_ref

            page_info = (((result or {}).get("team") or {}).get("issues") or {}).get(
                "pageInfo"
            )
            if not page_info or not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        return None

    async def _find_team_id(self, project_name: str) -> str | None:
        query = """
        query($cursor: String) {
          teams(first: 100, after: $cursor) {
            nodes {
              id
              name
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """
        cursor: str | None = None
        candidates = [project_name]
        if self.team_name:
            candidates.append(_normalize_status(self.team_name))
        while True:
            result = await self.api_client.graphql(query, {"cursor": cursor})
            teams = (((result or {}).get("teams") or {}).get("nodes") or [])
            for team in teams:
                if not isinstance(team, dict):
                    continue
                name = _normalize_status(str(team.get("name") or ""))
                team_id = team.get("id")
                if not isinstance(team_id, str):
                    continue
                if name in candidates:
                    return team_id
            page_info = (((result or {}).get("teams") or {}).get("pageInfo") or {})
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")

    async def _get_state_id(self, project_id: str, status: str) -> str | None:
        states = await self._get_project_states(project_id)
        normalized_status = _normalize_status(status)
        for status_name, state_id in states.items():
            if _normalize_status(status_name) == normalized_status:
                return state_id
        return None

    async def _get_project_states(self, project_id: str) -> dict[str, str]:
        if project_id in self._project_states_cache:
            return self._project_states_cache[project_id]

        query = """
        query($teamId: ID!, $cursor: String) {
          team(id: $teamId) {
            states(first: 100, after: $cursor) {
              nodes {
                id
                name
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        states: dict[str, str] = {}
        cursor: str | None = None
        while True:
            result = await self.api_client.graphql(
                query,
                {"teamId": project_id, "cursor": cursor},
            )
            state_nodes = (
                (((result or {}).get("team") or {}).get("states") or {}).get("nodes") or []
            )
            for state in state_nodes:
                if not isinstance(state, dict):
                    continue
                state_name = state.get("name")
                state_id = state.get("id")
                if isinstance(state_name, str) and isinstance(state_id, str):
                    states[state_name] = state_id
            page_info = (((result or {}).get("team") or {}).get("states") or {}).get("pageInfo")
            if not page_info or not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        self._project_states_cache[project_id] = states
        return states


def _to_issue_ref(
    node: dict[str, Any],
    *,
    fallback_repo_owner: str,
    fallback_repo_name: str,
) -> _LinearIssueRef:
    number = _safe_int(node.get("number")) or 0
    repo_owner, repo_name = _extract_repo(
        node.get("repository"),
        fallback_repo_owner,
        fallback_repo_name,
    )
    if not repo_owner or not repo_name:
        parsed_owner, parsed_repo = _extract_repo_from_url(node.get("url"))
        if parsed_owner:
            repo_owner = parsed_owner
        if parsed_repo:
            repo_name = parsed_repo
    return _LinearIssueRef(
        id=str(node.get("id") or ""),
        number=number,
        title=node.get("title") or "",
        repo_owner=repo_owner or None,
        repo_name=repo_name or None,
        status=_extract_state_name(node),
        html_url=node.get("url") or "",
    )


def _issue_from_ref(ref: _LinearIssueRef) -> Issue:
    return Issue(
        number=ref.number,
        title=ref.title,
        body="",
        labels=[],
        assignee=None,
        state=ref.status or "open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        html_url=ref.html_url,
        repo_owner=ref.repo_owner,
        repo_name=ref.repo_name,
    )


def _extract_state_name(node: Any) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    state = node.get("state")
    if isinstance(state, dict):
        status = state.get("name")
        if isinstance(status, str):
            return status
    status = node.get("stateName") or node.get("status")
    return status if isinstance(status, str) else ""


def _normalize_status(value: str) -> str:
    return (value or "").strip().lower()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_blocker_nodes(issue_node: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for key in ("blockedByIssues", "dependencies", "blockingIssues"):
        collection = issue_node.get(key)
        if not isinstance(collection, dict):
            continue
        nodes = collection.get("nodes")
        if isinstance(nodes, list):
            blockers.extend([node for node in nodes if isinstance(node, dict)])
    return blockers


def _extract_repo(
    repository_node: Any,
    fallback_owner: str,
    fallback_repo: str,
) -> tuple[str, str]:
    if isinstance(repository_node, dict):
        owner_node = repository_node.get("owner")
        owner = owner_node.get("login") if isinstance(owner_node, dict) else None
        name = repository_node.get("name")
        if isinstance(owner, str) and isinstance(name, str):
            return owner.strip(), name.strip()
    return fallback_owner or "", fallback_repo or ""


def _extract_repo_from_url(url: Any) -> tuple[str, str]:
    if not isinstance(url, str):
        return "", ""
    if "github.com/" not in url:
        return "", ""
    path = url.split("github.com/", 1)[-1]
    owner, sep, repo_and_tail = path.partition("/")
    if not sep:
        return "", ""
    repo, _sep, _tail = repo_and_tail.partition("/")
    if not owner or not repo:
        return "", ""
    return owner, repo
