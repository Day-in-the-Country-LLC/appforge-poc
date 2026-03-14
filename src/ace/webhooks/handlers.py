"""Webhook event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue
from ace.github.status_manager import IssueStatus
from ace.github.work_items import WorkBoardItem, WorkItemTracker
from ace.issue_tracking import build_work_item_tracker
from ace.pr_review.job_store import (
    build_pr_review_job_record,
    build_pr_review_job_store,
)
from ace.pr_review.pubsub_queue import PRReviewJob, PRReviewPubSubQueue
from ace.runners.agent_pool import AgentTarget, get_pool
from ace.webhooks.event_router import WorkEvent, WorkEventRouter
from ace.webhooks.github_app import GitHubAppAuth
from ace.webhooks.lifecycle import (
    STAGE_PR_REVIEW_ENQUEUED,
    STAGE_PR_REVIEW_ERROR,
    STAGE_PR_REVIEW_SKIPPED,
    STAGE_PR_REVIEW_STARTED,
)

logger = structlog.get_logger(__name__)


class WebhookHandler:
    """Dispatch and handle webhook events."""

    def __init__(
        self,
        settings: Any | None = None,
        app_auth: GitHubAppAuth | object | None = None,
        pr_review_queue: PRReviewPubSubQueue | None = None,
        pr_review_store: Any | None = None,
        event_router: WorkEventRouter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.app_auth = app_auth or GitHubAppAuth.from_env()
        self.pr_review_queue = pr_review_queue
        self.pr_review_store = pr_review_store
        self.event_router = event_router or WorkEventRouter()

    async def handle(
        self,
        event: str | WorkEvent,
        payload: dict[str, Any],
        delivery: str | None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        work_event = event if isinstance(event, WorkEvent) else self.event_router.route(
            source="github",
            event=event,
            payload=payload,
            delivery=delivery,
        )
        if work_event is None:
            logger.info("webhook_ignored", event=event, delivery=delivery)
            return {"status": "ignored", "event": event}

        handler = {
            "projects_v2_item": self._handle_projects_v2_item,
            "issue_comment": self._handle_issue_comment,
            "issues": self._handle_issue_event,
            "pull_request": self._handle_pull_request_event,
        }.get(work_event.event_type)

        if handler is None or not work_event.is_supported:
            logger.info(
                "webhook_ignored",
                webhook_event=work_event.event_type,
                webhook_source=work_event.source,
                action=work_event.action,
                delivery=delivery,
            )
            return {"status": "ignored", "reason": "unsupported_event"}

        if work_event.event_type == "pull_request":
            return await self._handle_pull_request_event(
                payload,
                delivery,
                workflow_id=workflow_id,
            )

        return await handler(payload, delivery)

    async def _handle_projects_v2_item(
        self, payload: dict[str, Any], delivery: str | None
    ) -> dict[str, Any]:
        installation_id = _extract_installation_id(payload)
        token = await self.app_auth.get_installation_token(installation_id)

        async with GitHubAPIClient(token.token) as api_client:
            tracker = build_work_item_tracker(
                api_client=api_client,
                owner=self.settings.github_org,
                repo="",
            )
            item = _extract_projects_item(payload)
            item_node_id = _extract_item_node_id(item)
            project_name = (
                _extract_project_name(payload)
                or self._default_project_name()
            )
            project_id = await tracker.get_project_id(project_name)
            if not project_id:
                raise ValueError("❌ ERROR: Project not found")

            event_project_id = _extract_project_node_id(payload)
            if (
                self.settings.issue_tracker_backend.lower() == "github"
                and event_project_id
                and event_project_id != project_id
            ):
                logger.info(
                    "project_item_ignored_wrong_project",
                    event_project_id=event_project_id,
                    expected_project_id=project_id,
                    delivery=delivery,
                )
                return {"status": "ignored", "reason": "wrong_project"}

            project_item: WorkBoardItem | None = None
            if item_node_id:
                project_item = await tracker.get_project_item_by_id(item_node_id)

            if project_item is None:
                content_node_id = _extract_content_node_id(item)
                if not content_node_id:
                    raise ValueError("❌ ERROR: project item missing content node id")
                issue_info = await _fetch_issue_info(api_client, content_node_id)
                if issue_info is None:
                    raise ValueError(
                        "❌ ERROR: unable to resolve issue from project item"
                    )
                item_id = await tracker.get_item_id_for_issue(
                    project_id,
                    issue_info.number,
                    issue_info.repo_owner,
                    issue_info.repo_name,
                )
                if not item_id:
                    raise ValueError("❌ ERROR: project item not found for issue")
                project_item = await tracker.get_project_item_by_id(item_id)

            if project_item is None:
                raise ValueError("❌ ERROR: project item lookup failed")

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
                    tracker,
                    IssueStatus.READY,
                    check_blockers=True,
                    project_name=project_name,
                    delivery=delivery,
                )

            if _is_in_progress_transition(transition):
                return await _trigger_project_issue(
                    project_item,
                    tracker,
                    IssueStatus.IN_PROGRESS,
                    check_blockers=True,
                    project_name=project_name,
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

        issue_info = _extract_issue_from_payload(payload)
        if issue_info is None:
            raise ValueError("❌ ERROR: issue_comment payload missing issue metadata")

        api_client = await self._get_github_api_client(payload)
        async with api_client:
            tracker = build_work_item_tracker(
                api_client=api_client,
                owner=self.settings.github_org,
                repo="",
            )
            full_issue = await tracker.get_issue(
                issue_info.number,
                repo_owner=issue_info.repo_owner,
                repo_name=issue_info.repo_name,
            )
            result = await _trigger_specific_issue(full_issue, check_blockers=False)
            return {
                "status": "triggered",
                "action": "pr_comment",
                "result": result,
                "issue": issue_info.number,
                "repo": f"{issue_info.repo_owner}/{issue_info.repo_name}",
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

        api_client = await self._get_github_api_client(payload)
        async with api_client:
            tracker = build_work_item_tracker(
                api_client=api_client,
                owner=self.settings.github_org,
                repo="",
            )
            project_name = self._default_project_name()
            ready_issues = await tracker.list_issues_by_project_status(
                project_name,
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

                blockers = await tracker.get_issue_blockers(
                    issue.repo_owner,
                    issue.repo_name,
                    issue.number,
                )
                if not _blockers_include_issue(blockers, closed_issue):
                    continue

                not_done = await _blockers_not_done(
                    tracker,
                    blockers,
                    project_name,
                )
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

            if not triggered:
                return {"status": "ignored", "reason": "no_trigger_candidates"}

            return {
                "status": "triggered",
                "action": "blocker_closed",
                "closed_issue": (
                    f"{closed_issue.repo_owner}/{closed_issue.repo_name}#{closed_issue.number}"
                ),
                "triggered_count": len(triggered),
                "results": triggered,
            }

    async def _handle_pull_request_event(
        self,
        payload: dict[str, Any],
        delivery: str | None,
        *,
        workflow_id: str | None,
    ) -> dict[str, Any]:
        action = (payload.get("action") or "").strip()
        if action == "closed":
            logger.info("webhook_ignored", event="pull_request", reason="action_not_reviewable")
            return {"status": "ignored", "reason": "action_not_reviewable"}

        pr = _extract_pull_request_from_payload(payload)
        if pr is None:
            raise ValueError("❌ ERROR: pull_request payload missing metadata")

        if not self._pr_review_enabled():
            logger.info(
                "webhook_lifecycle",
                stage=STAGE_PR_REVIEW_SKIPPED,
                reason="pr_review_disabled",
                action="pr_review",
                pr_number=pr.number,
                repo=f"{pr.repo_owner}/{pr.repo_name}",
            )
            return {"status": "ignored", "reason": "pr_review_disabled"}

        if not self._is_pull_request_repo_allowed(pr.repo_owner, pr.repo_name):
            logger.info(
                "webhook_lifecycle",
                stage=STAGE_PR_REVIEW_SKIPPED,
                reason="repo_not_allowed",
                action="pr_review",
                pr_number=pr.number,
                repo=f"{pr.repo_owner}/{pr.repo_name}",
            )
            return {"status": "ignored", "reason": "repo_not_allowed"}

        if pr.is_draft:
            logger.info(
                "webhook_lifecycle",
                stage=STAGE_PR_REVIEW_SKIPPED,
                reason="draft_pr",
                action="pr_review",
                pr_number=pr.number,
                repo=f"{pr.repo_owner}/{pr.repo_name}",
            )
            return {"status": "ignored", "reason": "draft_pr"}

        if not self._is_pull_request_reviewable_action(action):
            logger.info("webhook_ignored", event="pull_request", reason="action_not_reviewable")
            return {"status": "ignored", "reason": "action_not_reviewable"}

        resolved_workflow_id = workflow_id or _generate_workflow_id()
        logger.info(
            "webhook_lifecycle",
            stage=STAGE_PR_REVIEW_STARTED,
            action=action,
            pr_number=pr.number,
            repo=f"{pr.repo_owner}/{pr.repo_name}",
            workflow_id=resolved_workflow_id,
            delivery_id=delivery,
        )

        store = self._get_pr_review_store()
        installation_id = _extract_installation_id(payload)
        idempotency_key = _build_pr_review_idempotency_key(
            pr.repo_owner,
            pr.repo_name,
            pr.number,
            pr.head_sha,
        )
        record = build_pr_review_job_record(
            idempotency_key=idempotency_key,
            repo_owner=pr.repo_owner,
            repo_name=pr.repo_name,
            pr_number=pr.number,
            head_sha=pr.head_sha,
            base_ref=pr.base_ref,
            action=action,
            workflow_id=resolved_workflow_id,
            installation_id=installation_id,
            delivery_id=delivery,
            project=self.settings.github_project_name,
            target_gcp_project=None,
        )

        claim = await store.claim_job(record)
        if not claim.claimed:
            if claim.reason == "review_in_progress":
                logger.info(
                    "webhook_lifecycle",
                    stage=STAGE_PR_REVIEW_SKIPPED,
                    reason=claim.reason,
                    action="pr_review",
                    pr_number=pr.number,
                    repo=f"{pr.repo_owner}/{pr.repo_name}",
                    head_sha=pr.head_sha,
                    idempotency_key=idempotency_key,
                )
                return {
                    "status": "skipped",
                    "reason": claim.reason,
                    "action": "pr_review",
                    "pr_number": pr.number,
                    "repo": f"{pr.repo_owner}/{pr.repo_name}",
                    "head_sha": pr.head_sha,
                    "idempotency_key": idempotency_key,
                }
            raise ValueError(f"❌ ERROR: failed to claim PR review job ({claim.reason})")

        job = PRReviewJob(
            repo_owner=pr.repo_owner,
            repo_name=pr.repo_name,
            pr_number=pr.number,
            head_sha=pr.head_sha,
            base_ref=pr.base_ref,
            action=action,
            workflow_id=resolved_workflow_id,
            idempotency_key=idempotency_key,
            installation_id=installation_id,
            delivery_id=delivery,
            project=self.settings.github_project_name,
            target_gcp_project=None,
        )
        try:
            queue = await self._get_pr_review_queue()
            message_id = await queue.publish(job)
            queued_at = _utc_now_iso()
            await store.mark_enqueued(
                idempotency_key=idempotency_key,
                pubsub_message_id=message_id,
                queued_at=queued_at,
            )
            logger.info(
                "webhook_lifecycle",
                stage=STAGE_PR_REVIEW_ENQUEUED,
                action="pr_review",
                pubsub_message_id=message_id,
                pr_number=pr.number,
                repo=f"{pr.repo_owner}/{pr.repo_name}",
                head_sha=pr.head_sha,
                idempotency_key=idempotency_key,
            )
            return {
                "status": "enqueued",
                "action": "pr_review",
                "pr_number": pr.number,
                "repo": f"{pr.repo_owner}/{pr.repo_name}",
                "head_sha": pr.head_sha,
                "idempotency_key": idempotency_key,
                "message_id": message_id,
            }
        except Exception as exc:
            logger.info(
                "webhook_lifecycle",
                stage=STAGE_PR_REVIEW_ERROR,
                action="pr_review",
                reason="pr_review_publish_failed",
                error=str(exc),
                pr_number=pr.number,
                repo=f"{pr.repo_owner}/{pr.repo_name}",
                head_sha=pr.head_sha,
            )
            await store.release_claim(idempotency_key)
            raise

    def _pr_review_enabled(self) -> bool:
        return bool(getattr(self.settings, "pr_review_enabled", False))

    def _is_pull_request_reviewable_action(self, action: str) -> bool:
        return action in {"opened", "synchronize", "ready_for_review", "reopened"}

    def _is_pull_request_repo_allowed(self, repo_owner: str, repo_name: str) -> bool:
        allowlist = (getattr(self.settings, "pr_review_allowed_repos", "") or "").strip()
        if not allowlist:
            return True
        normalized_allowlist = [entry.strip().lower() for entry in allowlist.split(",") if entry.strip()]
        return f"{repo_owner}/{repo_name}".lower() in normalized_allowlist

    async def _get_pr_review_queue(self) -> PRReviewPubSubQueue:
        if self.pr_review_queue is None:
            self.pr_review_queue = PRReviewPubSubQueue.from_settings(self.settings)
        return self.pr_review_queue

    def _get_pr_review_store(self):
        if self.pr_review_store is None:
            self.pr_review_store = build_pr_review_job_store(self.settings)
        return self.pr_review_store

    def _default_project_name(self) -> str:
        if self.settings.issue_tracker_backend.lower() == "linear":
            return self.settings.linear_default_project_name or self.settings.github_project_name
        return self.settings.github_project_name

    async def _get_github_api_client(self, payload: dict[str, Any]) -> GitHubAPIClient:
        installation_id = _extract_installation_id(payload)
        token = await self.app_auth.get_installation_token(installation_id)
        return GitHubAPIClient(token.token)


@dataclass(frozen=True)
class IssueInfo:
    number: int
    repo_owner: str
    repo_name: str


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    repo_owner: str
    repo_name: str
    head_sha: str
    base_ref: str
    is_draft: bool


def _extract_installation_id(payload: dict[str, Any]) -> int:
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if not installation_id:
        raise ValueError("❌ ERROR: webhook payload missing installation id")
    return int(installation_id)


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


def _extract_projects_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item")
    if not item:
        raise ValueError("❌ ERROR: webhook payload missing projects_v2_item")
    if not isinstance(item, dict):
        raise ValueError("❌ ERROR: webhook payload missing projects_v2_item")
    return item


def _extract_issue_from_payload(payload: dict[str, Any]) -> IssueInfo | None:
    issue = payload.get("issue") or {}
    number = issue.get("number")
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    name = repo.get("name")
    if not number or not owner or not name:
        return None
    return IssueInfo(number=int(number), repo_owner=owner, repo_name=name)


@dataclass(frozen=True)
class StatusTransition:
    from_status: str | None
    to_status: str | None


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


def _extract_project_name(payload: dict[str, Any]) -> str | None:
    item = payload.get("project") or {}
    if isinstance(item, dict):
        return _extract_project_title(item)
    project = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    if isinstance(project, dict):
        return _extract_project_title(project)
    return None


def _extract_project_title(project: dict[str, Any]) -> str | None:
    for key in ("title", "name", "project_title", "projectName"):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_item_node_id(item: dict[str, Any]) -> str | None:
    for key in ("node_id", "nodeId", "item_node_id", "itemNodeId", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_content_node_id(item: dict[str, Any]) -> str | None:
    value = item.get("content_node_id") or item.get("contentNodeId")
    if isinstance(value, str) and value:
        return value
    content = item.get("content") or {}
    if not isinstance(content, dict):
        return None
    value = content.get("node_id") or content.get("nodeId") or content.get("id")
    return value if isinstance(value, str) and value else None


def _extract_pull_request_from_payload(payload: dict[str, Any]) -> PullRequestInfo | None:
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    number = pr.get("number")
    owner = (repo.get("owner") or {}).get("login")
    name = repo.get("name")
    head = pr.get("head") or {}
    head_sha = head.get("sha")
    base = pr.get("base") or {}
    base_ref = base.get("ref")
    if not number or not owner or not name or not head_sha or not base_ref:
        return None
    draft = bool(pr.get("draft", False))
    return PullRequestInfo(
        number=int(number),
        repo_owner=owner,
        repo_name=name,
        head_sha=str(head_sha),
        base_ref=str(base_ref),
        is_draft=draft,
    )


def _extract_status_transition(payload: dict[str, Any]) -> StatusTransition | None:
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict):
        return None
    field_value = changes.get("field_value") or changes.get("fieldValue") or {}
    if not isinstance(field_value, dict):
        return None
    field_name = field_value.get("field_name") or field_value.get("fieldName")
    if field_name and str(field_name).lower() != "status":
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
    return (transition.from_status or "").lower() == "backlog" and (
        transition.to_status or ""
    ).lower() == "ready"


def _is_in_progress_transition(transition: StatusTransition) -> bool:
    return (transition.from_status or "").lower() == "blocked" and (
        transition.to_status or ""
    ).lower() == "in progress"


def _build_pr_review_idempotency_key(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
) -> str:
    return f"{repo_owner}/{repo_name}:{pr_number}:{head_sha}"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _generate_workflow_id() -> str:
    return f"wf-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{int(datetime.now(UTC).timestamp())}"


async def _trigger_project_issue(
    project_item: WorkBoardItem,
    tracker: WorkItemTracker,
    expected_status: IssueStatus,
    *,
    check_blockers: bool,
    project_name: str,
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
        blockers = await tracker.get_issue_blockers(
            project_item.repo_owner,
            project_item.repo_name,
            project_item.number,
        )
        not_done = await _blockers_not_done(tracker, blockers, project_name)
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

    issue = await tracker.get_issue(
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
    tracker: WorkItemTracker,
    blockers,
    project_name: str,
) -> list[tuple[int, str | None]]:
    not_done = []
    for blocker in blockers:
        status = await tracker.get_issue_project_status(
            blocker.number,
            blocker.repo_owner,
            blocker.repo_name,
            project_name,
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
