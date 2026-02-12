"""Canonical lifecycle logging for webhook listener/worker processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog


STAGE_WEBHOOK_RECEIVED = "webhook_received"
STAGE_WEBHOOK_ENQUEUED = "webhook_enqueued"
STAGE_WORKER_DEQUEUED = "worker_dequeued"
STAGE_WORKER_STARTED = "worker_started"
STAGE_AGENT_STARTED = "agent_started"
STAGE_AGENT_FINISHED = "agent_finished"
STAGE_FINAL_RESOLUTION = "final_resolution"

RESOLUTION_SUCCESS = "success"
RESOLUTION_BLOCKED = "blocked"
RESOLUTION_FAILURE = "failure"
RESOLUTION_TIMEOUT = "timeout"
RESOLUTION_PR_OPENED = "pr_opened"


@dataclass(frozen=True)
class WebhookLifecycleContext:
    event: str
    action: str | None
    project: str | None
    issue_key: str | None
    delivery_id: str | None
    workflow_id: str


def build_lifecycle_context(
    *,
    event: str,
    payload: dict[str, Any],
    delivery: str | None,
    workflow_id: str | None = None,
    default_project: str | None = None,
) -> WebhookLifecycleContext:
    """Build a normalized lifecycle context for listener and worker logs."""
    normalized_delivery = delivery.strip() if isinstance(delivery, str) and delivery.strip() else None
    resolved_workflow_id = _resolve_workflow_id(workflow_id=workflow_id, delivery=normalized_delivery)
    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        action = None

    return WebhookLifecycleContext(
        event=event,
        action=action,
        project=_extract_project(payload, default_project=default_project),
        issue_key=_extract_issue_key(payload),
        delivery_id=normalized_delivery,
        workflow_id=resolved_workflow_id,
    )


def log_lifecycle_event(
    logger: structlog.BoundLogger,
    stage: str,
    context: WebhookLifecycleContext,
    *,
    resolution: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured lifecycle log entry with canonical correlation fields."""
    bound_logger = logger.bind(
        event=context.event,
        action=context.action,
        project=context.project,
        issue_key=context.issue_key,
        delivery_id=context.delivery_id,
        workflow_id=context.workflow_id,
    )
    data = {"stage": stage}
    if resolution is not None:
        data["resolution"] = resolution
    data.update(fields)
    bound_logger.info("webhook_lifecycle", **data)


def normalize_result_resolution(result: dict[str, Any]) -> str:
    """Map handler results to a normalized resolution value."""
    status = _to_lower(result.get("status"))
    action = _to_lower(result.get("action"))
    if status == "blocked":
        return RESOLUTION_BLOCKED
    if status in {"timeout", "task_wait_timeout"}:
        return RESOLUTION_TIMEOUT
    if status in {"failure", "failed", "error"}:
        return RESOLUTION_FAILURE
    if action == "pr_opened" or "pr_number" in result:
        return RESOLUTION_PR_OPENED

    nested = result.get("result")
    if isinstance(nested, dict):
        nested_status = _to_lower(nested.get("status"))
        if nested_status in {"timeout", "task_wait_timeout"}:
            return RESOLUTION_TIMEOUT
        if nested_status in {"failure", "failed", "error"}:
            return RESOLUTION_FAILURE
        if nested_status == "blocked":
            return RESOLUTION_BLOCKED
        if nested_status == "pr_opened" or "pr_number" in nested:
            return RESOLUTION_PR_OPENED

    return RESOLUTION_SUCCESS


def normalize_error_resolution(exc: Exception) -> str:
    """Normalize exception outcomes for lifecycle logging."""
    text = str(exc).lower()
    if "timeout" in text:
        return RESOLUTION_TIMEOUT
    return RESOLUTION_FAILURE


def _resolve_workflow_id(*, workflow_id: str | None, delivery: str | None) -> str:
    if isinstance(workflow_id, str) and workflow_id.strip():
        return workflow_id.strip()
    if delivery:
        return delivery
    return f"wf-{uuid4().hex}"


def _extract_project(payload: dict[str, Any], *, default_project: str | None) -> str | None:
    project = payload.get("project")
    if isinstance(project, dict):
        title = project.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()

    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    if isinstance(item, dict):
        for key in ("project_title", "projectTitle", "project_name", "projectName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if isinstance(default_project, str) and default_project.strip():
        return default_project.strip()
    return None


def _extract_issue_key(payload: dict[str, Any]) -> str | None:
    repo_owner, repo_name = _extract_repository(payload)
    issue = payload.get("issue")
    number = None
    if isinstance(issue, dict):
        number = issue.get("number")

    if number is None:
        item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
        if isinstance(item, dict):
            content = item.get("content")
            if isinstance(content, dict):
                number = content.get("number")
                if not repo_owner or not repo_name:
                    repo_owner, repo_name = _extract_repository({"repository": content.get("repository")})

    if number is None:
        return None
    try:
        number_int = int(number)
    except (TypeError, ValueError):
        return None

    if not repo_owner or not repo_name:
        return None
    return f"{repo_owner}/{repo_name}#{number_int}"


def _extract_repository(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return None, None

    name = repo.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, None

    owner = repo.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("login")
    if not isinstance(owner, str) or not owner.strip():
        return None, None
    return owner.strip(), name.strip()


def _to_lower(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
