"""Canonical lifecycle logging for webhook listener/worker processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

import structlog

STAGE_WEBHOOK_RECEIVED = "webhook_received"
STAGE_WEBHOOK_ENQUEUED = "webhook_enqueued"
STAGE_WORKER_DEQUEUED = "worker_dequeued"
STAGE_WORKER_STARTED = "worker_started"
STAGE_AGENT_STARTED = "agent_started"
STAGE_AGENT_FINISHED = "agent_finished"
STAGE_FINAL_RESOLUTION = "final_resolution"

STAGE_PLANNING_INTAKE = "planning_intake"
STAGE_PLANNING_SCOUTING = "planning_scouting"
STAGE_PLANNING_SYNTHESIS = "planning_synthesis"
STAGE_PLANNING_REVIEW = "planning_review"
STAGE_PLANNING_DONE = "planning_done"
STAGE_PLANNING_FAILED = "planning_failed"
STAGE_PLANNING_ISSUE_WRITER = "planning_issue_writer"

# PR review stages
STAGE_PR_REVIEW_STARTED = "pr_review_started"
STAGE_PR_REVIEW_CODEX_INITIAL = "pr_review_codex_initial"
STAGE_PR_REVIEW_CLAUDE_INITIAL = "pr_review_claude_initial"
STAGE_PR_REVIEW_CROSS_FEEDBACK = "pr_review_cross_feedback"
STAGE_PR_REVIEW_CONSENSUS = "pr_review_consensus"
STAGE_PR_REVIEW_APPROVAL_SUBMITTED = "pr_review_approval_submitted"
STAGE_PR_REVIEW_MERGED = "pr_review_merged"
STAGE_PR_REVIEW_REJECTED = "pr_review_rejected"
STAGE_PR_REVIEW_ENQUEUED = "pr_review_enqueued"
STAGE_PR_REVIEW_SKIPPED = "pr_review_skipped"
STAGE_PR_REVIEW_ERROR = "pr_review_error"

STAGE_SESSION_START = "session_start"
STAGE_SESSION_TURN_START = "session_turn_start"
STAGE_SESSION_TURN_COMPLETE = "session_turn_complete"
STAGE_SESSION_STALL = "session_stall"
STAGE_SESSION_RESUME = "session_resume"

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
    target_gcp_project: str | None
    delivery_id: str | None
    workflow_id: str
    source: str


@dataclass(frozen=True)
class PlanningLifecycleContext:
    """Structured context for planning lifecycle logging."""

    session_id: str
    project_slug: str
    phase: str
    mode: str | None
    request_id: str | None


@dataclass(frozen=True)
class SessionLifecycleContext:
    """Structured context for session-based runtime telemetry."""

    source: str
    issue_key: str | None
    session_id: str
    turn_number: int
    workflow_id: str
    project: str | None = None
    target_gcp_project: str | None = None
    action: str | None = None
    stage_total: int | None = None
    stage_index: int | None = None


def _normalize_source(source: str | None) -> str:
    normalized = (source or "").strip().lower()
    return normalized or "github"


def build_lifecycle_context(
    *,
    event: str,
    payload: dict[str, Any],
    delivery: str | None,
    workflow_id: str | None = None,
    default_project: str | None = None,
    project: str | None = None,
    issue_key: str | None = None,
    target_gcp_project: str | None = None,
    source: str | None = None,
    repo_gcp_mapping: Mapping[str, str] | None = None,
    action: str | None = None,
) -> WebhookLifecycleContext:
    """Build a normalized lifecycle context for listener and worker logs."""
    normalized_delivery = (
        delivery.strip() if isinstance(delivery, str) and delivery.strip() else None
    )
    resolved_workflow_id = _resolve_workflow_id(
        workflow_id=workflow_id,
        delivery=normalized_delivery,
    )
    resolved_action = action
    if not isinstance(resolved_action, str) or not resolved_action.strip():
        resolved_action = payload.get("action")
    if not isinstance(resolved_action, str) or not resolved_action.strip():
        resolved_action = None

    resolved_project = project
    if not isinstance(resolved_project, str) or not resolved_project.strip():
        resolved_project = _extract_project(payload, default_project=default_project)
    else:
        resolved_project = resolved_project.strip()

    resolved_issue_key = issue_key
    if not isinstance(resolved_issue_key, str) or not resolved_issue_key.strip():
        resolved_issue_key = _extract_issue_key(payload)
    else:
        resolved_issue_key = resolved_issue_key.strip()

    resolved_target_gcp_project = _resolve_target_gcp_project(
        payload=payload,
        issue_key=resolved_issue_key,
        target_gcp_project=target_gcp_project,
        repo_gcp_mapping=repo_gcp_mapping,
    )

    return WebhookLifecycleContext(
        event=event,
        action=resolved_action,
        project=resolved_project,
        issue_key=resolved_issue_key,
        target_gcp_project=resolved_target_gcp_project,
        delivery_id=normalized_delivery,
        workflow_id=resolved_workflow_id,
        source=_normalize_source(source),
    )


def build_session_lifecycle_context(
    *,
    session_id: str,
    turn_number: int,
    workflow_id: str,
    source: str | None = None,
    issue_key: str | None = None,
    project: str | None = None,
    target_gcp_project: str | None = None,
    action: str | None = None,
    stage_total: int | None = None,
    stage_index: int | None = None,
) -> SessionLifecycleContext:
    """Build a normalized session context for runtime lifecycle telemetry."""
    session_value = (session_id or "").strip()
    if not session_value:
        raise ValueError("❌ ERROR: session_id is required for session telemetry")

    if not isinstance(turn_number, int) or turn_number < 1:
        raise ValueError(
            "❌ ERROR: turn_number is required for session telemetry and must be > 0"
        )

    workflow_value = (workflow_id or "").strip()
    if not workflow_value:
        raise ValueError("❌ ERROR: workflow_id is required for session telemetry")

    issue_value = issue_key.strip() if isinstance(issue_key, str) and issue_key.strip() else None

    return SessionLifecycleContext(
        source=_normalize_source(source),
        issue_key=issue_value,
        session_id=session_value,
        turn_number=turn_number,
        workflow_id=workflow_value,
        project=project,
        target_gcp_project=target_gcp_project,
        action=action,
        stage_total=stage_total,
        stage_index=stage_index,
    )


def build_planning_lifecycle_context(
    *,
    session_id: str,
    project_slug: str,
    phase: str,
    mode: str | None = None,
    request_id: str | None = None,
) -> PlanningLifecycleContext:
    """Build planning-specific lifecycle context for structured logs."""
    session_value = (
        session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
    )
    if not session_value:
        raise ValueError("❌ ERROR: session_id is required for planning lifecycle context")

    project_value = (
        project_slug.strip() if isinstance(project_slug, str) and project_slug.strip() else ""
    )
    normalized_phase = phase.strip().lower() if isinstance(phase, str) else ""
    normalized_mode = mode.strip() if isinstance(mode, str) and mode.strip() else None
    normalized_request_id = (
        request_id.strip() if isinstance(request_id, str) and request_id.strip() else None
    )

    if not normalized_phase:
        raise ValueError("❌ ERROR: planning phase is required")

    return PlanningLifecycleContext(
        session_id=session_value,
        project_slug=project_value,
        phase=normalized_phase,
        mode=normalized_mode,
        request_id=normalized_request_id,
    )


def log_planning_lifecycle_event(
    logger: structlog.BoundLogger,
    stage: str,
    context: PlanningLifecycleContext,
    *,
    resolution: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured planning lifecycle event."""
    bound_logger = logger.bind(
        session_id=context.session_id,
        project_slug=context.project_slug,
        phase=context.phase,
        mode=context.mode,
    )
    if context.request_id:
        bound_logger = bound_logger.bind(request_id=context.request_id)

    data = {"stage": stage}
    if resolution is not None:
        data["resolution"] = resolution
    data.update(fields)
    bound_logger.info("planning_lifecycle", **data)


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
        source=context.source,
        event=context.event,
        action=context.action,
        project=context.project,
        issue_key=context.issue_key,
        target_gcp_project=context.target_gcp_project,
        delivery_id=context.delivery_id,
        workflow_id=context.workflow_id,
    )
    data = {"stage": stage}
    if resolution is not None:
        data["resolution"] = resolution
    data.update(fields)
    bound_logger.info("webhook_lifecycle", **data)


def log_session_lifecycle_event(
    logger: structlog.BoundLogger,
    stage: str,
    context: SessionLifecycleContext,
    *,
    resolution: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured session lifecycle event."""
    bound_logger = logger.bind(
        source=context.source,
        workflow_id=context.workflow_id,
        issue_key=context.issue_key,
        project=context.project,
        target_gcp_project=context.target_gcp_project,
        session_id=context.session_id,
        turn_number=context.turn_number,
        action=context.action,
        stage_total=context.stage_total,
        stage_index=context.stage_index,
    )

    data = {"stage": stage}
    if resolution is not None:
        data["resolution"] = resolution
    data.update(fields)
    bound_logger.info("session_lifecycle", **data)


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


def _resolve_target_gcp_project(
    *,
    payload: dict[str, Any],
    issue_key: str | None,
    target_gcp_project: str | None,
    repo_gcp_mapping: Mapping[str, str] | None,
) -> str | None:
    if target_gcp_project is not None:
        if not isinstance(target_gcp_project, str) or not target_gcp_project.strip():
            raise ValueError(
                "❌ ERROR: target_gcp_project must be a non-empty string when provided"
            )
        return target_gcp_project.strip()

    if repo_gcp_mapping is None:
        return None

    repo_owner, repo_name = _extract_repository(payload)
    if (not repo_owner or not repo_name) and issue_key:
        repo_owner, repo_name = _extract_repository_from_issue_key(issue_key)
    if not repo_owner or not repo_name:
        return None

    repo_key = f"{repo_owner}/{repo_name}".lower()
    mapped = repo_gcp_mapping.get(repo_key)
    if not mapped:
        raise ValueError(
            "❌ ERROR: repo_gcp_project_mapping_missing: "
            f"no mapping found for repository {repo_owner}/{repo_name}"
        )
    return mapped


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
                    repo_owner, repo_name = _extract_repository(
                        {"repository": content.get("repository")}
                    )

    if number is None:
        return None
    try:
        number_int = int(number)
    except (TypeError, ValueError):
        return None

    if not repo_owner or not repo_name:
        return None
    return f"{repo_owner}/{repo_name}#{number_int}"


def _extract_repository_from_issue_key(issue_key: str) -> tuple[str | None, str | None]:
    if "#" not in issue_key:
        return None, None
    repo_part, _sep, _number = issue_key.partition("#")
    if "/" not in repo_part:
        return None, None
    owner, _slash, name = repo_part.partition("/")
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        return None, None
    return owner, name


def _extract_repository(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    repo = payload.get("repository")
    resolved = _parse_repository(repo)
    if resolved != (None, None):
        return resolved

    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            resolved = _parse_repository(content.get("repository"))
            if resolved != (None, None):
                return resolved
    return None, None


def _parse_repository(repo: Any) -> tuple[str | None, str | None]:
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
