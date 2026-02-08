"""Webhook event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue, IssueQueue
from ace.github.projects_v2 import ProjectItem, ProjectsV2Client
from ace.github.status_manager import IssueStatus
from ace.runners.agent_pool import AgentTarget, get_pool
from ace.webhooks.github_app import GitHubAppAuth

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StatusTransition:
    from_status: str | None
    to_status: str | None


class WebhookHandler:
    """Dispatch and handle GitHub webhook events."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.app_auth = GitHubAppAuth.from_env()

    async def handle(self, event: str, payload: dict[str, Any], delivery: str | None) -> dict[str, Any]:
        if event == "projects_v2_item":
            return await self._handle_projects_v2_item(payload, delivery)
        if event == "issue_comment":
            return await self._handle_issue_comment(payload, delivery)
        if event == "issues":
            return await self._handle_issue_event(payload, delivery)

        logger.info("webhook_ignored", event=event, delivery=delivery)
        return {"status": "ignored", "event": event}

    async def _handle_projects_v2_item(
        self, payload: dict[str, Any], delivery: str | None
    ) -> dict[str, Any]:
        installation_id = _extract_installation_id(payload)
        token = await self.app_auth.get_installation_token(installation_id)

        async with GitHubAPIClient(token.token) as api_client:
            projects_client = ProjectsV2Client(api_client)
            project_id = await _resolve_project_id(payload, projects_client, self.settings)
            event_project_id = _extract_project_node_id(payload)
            if event_project_id and event_project_id != project_id:
                logger.info(
                    "project_item_ignored_wrong_project",
                    event_project_id=event_project_id,
                    expected_project_id=project_id,
                    delivery=delivery,
                )
                return {"status": "ignored", "reason": "wrong_project"}
            item = _extract_projects_item(payload)
            item_node_id = _extract_item_node_id(item)

            project_item: ProjectItem | None = None
            if item_node_id:
                project_item = await projects_client.get_project_item_by_id(item_node_id)

            if project_item is None:
                content_node_id = _extract_content_node_id(item)
                if not content_node_id:
                    raise ValueError("❌ ERROR: project item missing content node id")
                issue_info = await _fetch_issue_info(api_client, content_node_id)
                if issue_info is None:
                    raise ValueError("❌ ERROR: unable to resolve issue from project item")
                item_id = await projects_client.get_item_id_for_issue(
                    project_id,
                    issue_info.number,
                    issue_info.repo_owner,
                    issue_info.repo_name,
                )
                if not item_id:
                    raise ValueError("❌ ERROR: project item not found for issue")
                project_item = await projects_client.get_project_item_by_id(item_id)

            if project_item is None:
                raise ValueError("❌ ERROR: project item lookup failed")

            issue_queue = IssueQueue(api_client, self.settings.github_org, "", projects_client)
            transition = _extract_status_transition(payload)
            if not transition:
                logger.info(
                    "project_item_status_change_missing",
                    delivery=delivery,
                    item_id=project_item.item_id,
                )
                return {"status": "ignored", "reason": "status_change_missing"}

            if _is_ready_transition(transition):
                return await _trigger_project_issue(
                    project_item,
                    issue_queue,
                    projects_client,
                    project_id,
                    IssueStatus.READY,
                    check_blockers=True,
                    delivery=delivery,
                )

            if _is_in_progress_transition(transition):
                return await _trigger_project_issue(
                    project_item,
                    issue_queue,
                    projects_client,
                    project_id,
                    IssueStatus.IN_PROGRESS,
                    check_blockers=True,
                    delivery=delivery,
                )

            logger.info(
                "project_item_status_ignored",
                from_status=transition.from_status,
                to_status=transition.to_status,
                delivery=delivery,
            )
            return {"status": "ignored", "reason": "no_matching_transition"}

    async def _handle_issue_comment(
        self, payload: dict[str, Any], delivery: str | None
    ) -> dict[str, Any]:
        action = payload.get("action")
        if action != "created":
            logger.info("issue_comment_ignored", action=action, delivery=delivery)
            return {"status": "ignored", "reason": "action_not_created"}

        issue = payload.get("issue") or {}
        if "pull_request" not in issue:
            logger.info("issue_comment_not_pr", delivery=delivery)
            return {"status": "ignored", "reason": "not_pr"}

        issue = _extract_issue_from_payload(payload)
        if issue is None:
            raise ValueError("❌ ERROR: issue_comment payload missing issue metadata")

        installation_id = _extract_installation_id(payload)
        token = await self.app_auth.get_installation_token(installation_id)

        async with GitHubAPIClient(token.token) as api_client:
            issue_queue = IssueQueue(api_client, self.settings.github_org, "")
            full_issue = await issue_queue.get_issue(
                issue.number,
                repo_owner=issue.repo_owner,
                repo_name=issue.repo_name,
            )
            result = await _trigger_specific_issue(full_issue, check_blockers=False)
            return {
                "status": "triggered",
                "action": "pr_comment",
                "result": result,
                "issue": issue.number,
                "repo": f"{issue.repo_owner}/{issue.repo_name}",
            }

    async def _handle_issue_event(
        self, payload: dict[str, Any], delivery: str | None
    ) -> dict[str, Any]:
        action = payload.get("action")
        if action != "closed":
            logger.info("issues_event_ignored", action=action, delivery=delivery)
            return {"status": "ignored", "reason": "action_not_closed"}

        closed_issue = _extract_issue_from_payload(payload)
        if closed_issue is None:
            raise ValueError("❌ ERROR: issues payload missing issue metadata")

        installation_id = _extract_installation_id(payload)
        token = await self.app_auth.get_installation_token(installation_id)

        async with GitHubAPIClient(token.token) as api_client:
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, self.settings.github_org, "", projects_client)
            project_id = await projects_client.get_org_project_id(
                self.settings.github_org,
                self.settings.github_project_name,
            )
            if not project_id:
                raise ValueError("❌ ERROR: GitHub Project not found")

            ready_issues = await issue_queue.list_issues_by_project_status(
                self.settings.github_project_name,
                IssueStatus.READY.value,
            )

            pool = get_pool(AgentTarget.REMOTE)
            triggered: list[dict[str, Any]] = []

            for issue in ready_issues:
                if pool.get_status().idle_slots == 0:
                    logger.info("issue_close_no_capacity", delivery=delivery)
                    break

                if not issue.repo_owner or not issue.repo_name:
                    logger.info("issue_missing_repo", issue=issue.number)
                    continue

                blockers = await projects_client.get_issue_blockers(
                    issue.repo_owner,
                    issue.repo_name,
                    issue.number,
                )
                if not _blockers_include_issue(blockers, closed_issue):
                    continue

                not_done = await _blockers_not_done(projects_client, project_id, blockers)
                if not_done:
                    logger.info(
                        "issue_still_blocked",
                        issue=issue.number,
                        blockers=not_done,
                    )
                    continue

                result = await _trigger_specific_issue(issue, check_blockers=False)
                triggered.append(
                    {
                        "issue": issue.number,
                        "repo": f"{issue.repo_owner}/{issue.repo_name}",
                        "result": result,
                    }
                )

            return {
                "status": "triggered",
                "action": "blocker_closed",
                "closed_issue": f"{closed_issue.repo_owner}/{closed_issue.repo_name}#{closed_issue.number}",
                "triggered_count": len(triggered),
                "results": triggered,
            }


@dataclass(frozen=True)
class IssueInfo:
    number: int
    repo_owner: str
    repo_name: str


def _extract_projects_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item")
    if not item:
        raise ValueError("❌ ERROR: webhook payload missing projects_v2_item")
    return item


def _extract_installation_id(payload: dict[str, Any]) -> int:
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if not installation_id:
        raise ValueError("❌ ERROR: webhook payload missing installation id")
    return int(installation_id)


def _extract_item_node_id(item: dict[str, Any]) -> str | None:
    for key in ("node_id", "nodeId", "item_node_id", "itemNodeId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    value = item.get("id")
    if isinstance(value, str) and value and not value.isdigit():
        return value
    return None


def _extract_project_node_id(payload: dict[str, Any]) -> str | None:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    for key in ("project_node_id", "projectNodeId", "project_id", "projectId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    project = payload.get("project") or {}
    for key in ("node_id", "nodeId", "id"):
        value = project.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_content_node_id(item: dict[str, Any]) -> str | None:
    for key in ("content_node_id", "contentNodeId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    content = item.get("content") or {}
    for key in ("node_id", "nodeId", "id"):
        value = content.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def _fetch_issue_info(api_client: GitHubAPIClient, node_id: str) -> IssueInfo | None:
    query = """
    query($nodeId: ID!) {
        node(id: $nodeId) {
            ... on Issue {
                number
                repository {
                    owner { login }
                    name
                }
            }
            ... on PullRequest {
                number
                repository {
                    owner { login }
                    name
                }
            }
        }
    }
    """
    result = await api_client.graphql(query, {"nodeId": node_id})
    node = result.get("node") or {}
    number = node.get("number")
    repo = node.get("repository") or {}
    owner = repo.get("owner") or {}
    if not number or not owner.get("login") or not repo.get("name"):
        return None
    return IssueInfo(number=int(number), repo_owner=owner["login"], repo_name=repo["name"])


async def _resolve_project_id(
    payload: dict[str, Any],
    projects_client: ProjectsV2Client,
    settings,
) -> str:
    item = payload.get("projects_v2_item") or {}
    for key in ("project_node_id", "projectNodeId", "project_id", "projectId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    project_id = await projects_client.get_org_project_id(
        settings.github_org,
        settings.github_project_name,
    )
    if not project_id:
        raise ValueError("❌ ERROR: GitHub Project not found")
    return project_id


def _extract_status_transition(payload: dict[str, Any]) -> StatusTransition | None:
    changes = payload.get("changes") or {}
    field_value = changes.get("field_value") or changes.get("fieldValue") or {}
    if not field_value:
        return None
    field_name = field_value.get("field_name") or field_value.get("fieldName")
    if field_name and field_name.lower() != "status":
        return None
    from_value = field_value.get("from") or field_value.get("from_value")
    to_value = field_value.get("to") or field_value.get("to_value")
    from_status = _normalize_status(from_value)
    to_status = _normalize_status(to_value)
    if not from_status and not to_status:
        return None
    return StatusTransition(from_status=from_status, to_status=to_status)


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "option", "label"):
            if value.get(key):
                return str(value.get(key))
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _is_ready_transition(transition: StatusTransition) -> bool:
    return (
        (transition.from_status or "").lower() == "backlog"
        and (transition.to_status or "").lower() == "ready"
    )


def _is_in_progress_transition(transition: StatusTransition) -> bool:
    return (
        (transition.from_status or "").lower() == "blocked"
        and (transition.to_status or "").lower() == "in progress"
    )


def _extract_issue_from_payload(payload: dict[str, Any]) -> IssueInfo | None:
    issue = payload.get("issue") or {}
    number = issue.get("number")
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    name = repo.get("name")
    if not number or not owner or not name:
        return None
    return IssueInfo(number=int(number), repo_owner=owner, repo_name=name)


async def _trigger_project_issue(
    project_item: ProjectItem,
    issue_queue: IssueQueue,
    projects_client: ProjectsV2Client,
    project_id: str,
    expected_status: IssueStatus,
    *,
    check_blockers: bool,
    delivery: str | None,
) -> dict[str, Any]:
    if project_item.content_type != "Issue":
        logger.info(
            "project_item_not_issue",
            item_id=project_item.item_id,
            content_type=project_item.content_type,
            delivery=delivery,
        )
        return {"status": "ignored", "reason": "not_issue"}

    if project_item.status and project_item.status.lower() != expected_status.value.lower():
        logger.info(
            "project_item_status_mismatch",
            item_id=project_item.item_id,
            status=project_item.status,
            expected=expected_status.value,
            delivery=delivery,
        )
        return {"status": "ignored", "reason": "status_mismatch"}

    if not project_item.repo_owner or not project_item.repo_name:
        raise ValueError("❌ ERROR: project item missing repository metadata")

    if check_blockers:
        blockers = await projects_client.get_issue_blockers(
            project_item.repo_owner,
            project_item.repo_name,
            project_item.number,
        )
        not_done = await _blockers_not_done(projects_client, project_id, blockers)
        if not_done:
            logger.info(
                "issue_blocked_by_dependencies",
                issue=project_item.number,
                blockers=not_done,
            )
            return {
                "status": "blocked",
                "reason": "dependencies",
                "blockers": not_done,
                "issue": project_item.number,
                "repo": f"{project_item.repo_owner}/{project_item.repo_name}",
            }

    issue = await issue_queue.get_issue(
        project_item.number,
        repo_owner=project_item.repo_owner,
        repo_name=project_item.repo_name,
    )
    result = await _trigger_specific_issue(issue, check_blockers=False)
    return {
        "status": "triggered",
        "action": expected_status.value,
        "result": result,
        "issue": project_item.number,
        "repo": f"{project_item.repo_owner}/{project_item.repo_name}",
    }


async def _trigger_specific_issue(issue: Issue, *, check_blockers: bool) -> dict[str, Any]:
    pool = get_pool(AgentTarget.REMOTE)
    return await pool.process_issue(issue, check_blockers=check_blockers)


def _blockers_include_issue(blockers, target: IssueInfo) -> bool:
    for blocker in blockers:
        if (
            blocker.number == target.number
            and blocker.repo_owner == target.repo_owner
            and blocker.repo_name == target.repo_name
        ):
            return True
    return False


async def _blockers_not_done(
    projects_client: ProjectsV2Client,
    project_id: str,
    blockers,
) -> list[tuple[int, str | None]]:
    not_done = []
    for blocker in blockers:
        status = await projects_client.get_issue_project_status(
            project_id,
            blocker.number,
            blocker.repo_owner,
            blocker.repo_name,
        )
        if status != IssueStatus.DONE.value:
            not_done.append((blocker.number, status))
    return not_done


async def _trigger_ready_processing() -> dict[str, Any]:
    pool = get_pool(AgentTarget.REMOTE)
    pool.set_max_issues_per_run(1)
    return await pool.process_ready_issues()


async def _trigger_in_progress_processing() -> dict[str, Any]:
    pool = get_pool(AgentTarget.REMOTE)
    pool.set_max_issues_per_run(1)
    return await pool.process_in_progress_issues()
