"""HTTP API for planning sessions."""

from __future__ import annotations

import hmac as _hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from ace.agents.llm_client import call_claude, call_openai
from ace.config.secrets import resolve_claude_api_key, resolve_openai_api_key
from ace.config.settings import Settings, get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.planning.artifacts import (
    GCSPlanningArtifactStore,
    PlanningArtifactStore,
    PlanningArtifactStoreError,
)
from ace.planning.intake import generate_intake_questions, intake_complete
from ace.planning.issue_writer import (
    parse_issues_payload,
    write_issues_from_payload,
)
from ace.planning.models import (
    PlanningArtifact,
    PlanningArtifactList,
    PlanningArtifactType,
    PlanningEvent,
    PlanningEventList,
    PlanningIssueApprovalFailure,
    PlanningIssueApprovalRequest,
    PlanningIssueApprovalResponse,
    PlanningIssueApprovalSuccess,
    PlanningMessage,
    PlanningMessageCreate,
    PlanningMode,
    PlanningSession,
    PlanningSessionCreateRequest,
)
from ace.planning.pubsub_queue import PubSubPlannerQueue, decode_pubsub_push
from ace.planning.scouts import (
    PlanningArtifacts,
    PlanningScoutError,
    build_planning_artifacts,
    load_project_registry,
    run_repositories_scout,
)
from ace.planning.store_firestore import (
    PLANNING_STATUS_DONE,
    PLANNING_STATUS_EXPIRED,
    PLANNING_STATUS_FAILED,
    PLANNING_STATUS_INTAKE_PENDING,
    PLANNING_STATUS_READY_TO_RUN,
    PLANNING_STATUS_RUNNING,
    PLANNING_STATUS_TIMED_OUT,
    PlanningStore,
    PlanningStoreError,
    build_planning_store,
)
from ace.webhooks.lifecycle import (
    STAGE_PLANNING_DONE,
    STAGE_PLANNING_FAILED,
    STAGE_PLANNING_INTAKE,
    STAGE_PLANNING_ISSUE_WRITER,
    STAGE_PLANNING_REVIEW,
    STAGE_PLANNING_SCOUTING,
    STAGE_PLANNING_SYNTHESIS,
    build_planning_lifecycle_context,
    log_planning_lifecycle_event,
)


def _require_planning_api_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    required_token = (get_settings().planner_api_token or "").strip()
    if not required_token:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="❌ ERROR: missing Authorization bearer token")
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="❌ ERROR: malformed Authorization header")
    token = parts[1].strip()
    if not token or not _hmac.compare_digest(token, required_token):
        raise HTTPException(status_code=401, detail="❌ ERROR: invalid Authorization bearer token")


planning_router = APIRouter(
    prefix="/planning",
    tags=["planning"],
    dependencies=[Depends(_require_planning_api_token)],
)
planning_worker_router = APIRouter(tags=["planning"])

_PLANNING_INTAKE_TTL_HOURS = 24
_PLANNING_RUNNING_TTL_HOURS = 1
_CLEANUP_DEBOUNCE_SECONDS = 300
_last_cleanup_time: float = 0.0
_store: PlanningStore | None = None
_planner_queue: PubSubPlannerQueue | None = None
_artifact_store: PlanningArtifactStore | None = None
_planner_pipeline: Any | None = None
logger = structlog.get_logger(__name__)

PLANNING_PHASE_INTAKE = "intake"
PLANNING_PHASE_SCOUTING = "scouting"
PLANNING_PHASE_SYNTHESIS = "synthesis"
PLANNING_PHASE_REVIEW = "review"
PLANNING_PHASE_ISSUES = "issues"
PLANNING_PHASE_DONE = "done"


def _planner_enabled() -> bool:
    role = (get_settings().webhook_service_role or "").strip().lower()
    return role in ("planner", "both", "all")


def _planner_worker_enabled() -> bool:
    role = (get_settings().webhook_service_role or "").strip().lower()
    return role in ("planner", "worker", "both", "all")


def _require_planner() -> None:
    if not _planner_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: planner endpoint disabled")


def _require_planner_worker() -> None:
    if not _planner_worker_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: planner worker endpoint disabled")


def _resolve_store() -> PlanningStore:
    global _store
    if _store is None:
        try:
            _store = build_planning_store(get_settings())
        except PlanningStoreError as exc:
            raise HTTPException(status_code=500, detail=f"{exc}") from exc
    return _store


def _resolve_planner_queue() -> PubSubPlannerQueue:
    global _planner_queue
    if _planner_queue is None:
        _planner_queue = PubSubPlannerQueue.from_settings(get_settings())
    return _planner_queue


def _resolve_artifact_store() -> PlanningArtifactStore:
    global _artifact_store
    if _artifact_store is None:
        _artifact_store = GCSPlanningArtifactStore.from_settings(get_settings())
    return _artifact_store


def _reset_store_for_tests(store: PlanningStore | None = None) -> None:
    global _store
    _store = store


def _reset_planner_queue_for_tests(queue: PubSubPlannerQueue | None = None) -> None:
    global _planner_queue
    _planner_queue = queue


def _reset_artifact_store_for_tests(
    store: PlanningArtifactStore | None = None,
) -> None:
    global _artifact_store
    _artifact_store = store


def _reset_planner_pipeline_for_tests(pipeline: Any | None = None) -> None:
    global _planner_pipeline
    _planner_pipeline = pipeline


async def _default_plan_pipeline(
    session: PlanningSession,
) -> tuple[list[tuple[PlanningArtifactType, str]], dict[PlanningArtifactType, str]]:
    settings = get_settings()
    project = await load_project_registry(session.project_slug, settings=settings)
    if not project.repos:
        raise PlanningScoutError(
            f"❌ ERROR: project registry '{project.project_slug}' has no repos"
        )
    scout_reports = await run_repositories_scout(
        project.repos,
        github_token=(settings.github_token or ""),
    )
    artifacts: PlanningArtifacts = build_planning_artifacts(
        session=session,
        project=project,
        scout_reports=scout_reports,
    )
    artifact_store = _resolve_artifact_store()
    plan_url = await artifact_store.write_plan_markdown(
        session_id=session.id,
        content=artifacts.plan_markdown,
    )
    issues_url = await artifact_store.write_issues_json(
        session_id=session.id,
        content=artifacts.issues_json,
    )
    dependencies_url = await artifact_store.write_dependencies_mmd(
        session_id=session.id,
        content=artifacts.dependencies_mmd,
    )
    artifact_rows = [
        (PlanningArtifactType.PLAN_MARKDOWN, plan_url),
        (PlanningArtifactType.ISSUES_JSON, issues_url),
        (PlanningArtifactType.DEPENDENCIES_MMD, dependencies_url),
    ]
    artifact_payloads: dict[PlanningArtifactType, str] = {
        PlanningArtifactType.PLAN_MARKDOWN: artifacts.plan_markdown,
        PlanningArtifactType.ISSUES_JSON: artifacts.issues_json,
        PlanningArtifactType.DEPENDENCIES_MMD: artifacts.dependencies_mmd,
    }
    return artifact_rows, artifact_payloads


def _coerce_pipeline_output(
    pipeline_output: Any,
) -> tuple[list[tuple[PlanningArtifactType, str]], dict[PlanningArtifactType, str]]:
    if isinstance(pipeline_output, tuple) and len(pipeline_output) == 2:
        artifact_rows, payloads = pipeline_output
        if isinstance(artifact_rows, list) and isinstance(payloads, dict):
            return artifact_rows, payloads
        raise TypeError("❌ ERROR: planner pipeline output must be (artifacts, payloads)")
    if isinstance(pipeline_output, list):
        return pipeline_output, {}
    raise TypeError("❌ ERROR: planner pipeline output must be artifact list or tuple")


def _extract_json_payload(raw: str) -> Any:
    """Return the first JSON object/array found in LLM response text."""
    if not isinstance(raw, str):
        raise ValueError("❌ ERROR: expected model response to be text")

    text = raw.strip()
    if not text:
        raise ValueError("❌ ERROR: model response was empty")

    seen: set[str] = set()
    candidates: list[str] = []

    def _add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    if "```" in text:
        chunks = text.split("```")
        for chunk in chunks:
            candidate = chunk.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].lstrip()
            if candidate.startswith("{") and candidate.endswith("}"):
                _add(candidate)
            elif candidate.startswith("[") and candidate.endswith("]"):
                _add(candidate)

    if text.startswith("{") and text.endswith("}"):
        _add(text)
    elif text.startswith("[") and text.endswith("]"):
        _add(text)
    else:
        first_curly = text.find("{")
        last_curly = text.rfind("}")
        if first_curly != -1 and last_curly > first_curly:
            _add(text[first_curly : last_curly + 1])
        first_square = text.find("[")
        last_square = text.rfind("]")
        if first_square != -1 and last_square > first_square:
            _add(text[first_square : last_square + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError("❌ ERROR: model response did not contain valid JSON")


def _truncate_payload(payload: str, *, max_len: int) -> str:
    if not payload:
        return ""
    if len(payload) <= max_len:
        return payload
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict) and "issues" in parsed:
            issues = parsed["issues"]
            if isinstance(issues, list):
                while issues and len(json.dumps(parsed)) > max_len:
                    issues.pop()
                return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return f"{payload[:max_len]}..."


def _format_review_prompt(
    *,
    session: PlanningSession,
    plan_markdown: str,
    issues_json: str,
) -> str:
    """Build the prompt for the first-pass Claude review."""
    return (
        "You are the Planning Quality Reviewer.\n\n"
        f"Session ID: {session.id}\n"
        f"Project: {session.project_slug}\n"
        f"Mode: {session.mode.value}\n\n"
        "Review this planning result holistically and return JSON with:\n"
        "- plan_recommendations (array of strings)\n"
        "- issue_recommendations (array of objects: issue_id,\n"
        "recommended_title, recommended_description, reason)\n"
        "- overall_feedback (string)\n\n"
        "Keep recommendations concrete and action-oriented.\n\n"
        "PLAN.md:\n"
        "```markdown\n"
        f"{_truncate_payload(plan_markdown, max_len=12000)}\n"
        "```\n\n"
        "ISSUES.json:\n"
        "```json\n"
        f"{_truncate_payload(issues_json, max_len=12000)}\n"
        "```\n"
    )


def _format_revision_prompt(
    *,
    session: PlanningSession,
    plan_markdown: str,
    original_issues_json: str,
    review_payload: dict[str, Any],
) -> str:
    """Build the prompt for the second-pass issue JSON revision."""
    recommendations = json.dumps(review_payload, indent=2, sort_keys=True)
    return (
        "You are the Planning Issue Editor.\n\n"
        f"Session ID: {session.id}\n"
        f"Project: {session.project_slug}\n"
        f"Mode: {session.mode.value}\n\n"
        "Use the recommendations below to revise the planning issues.\n"
        'Return only valid JSON with this exact shape: {"issues": [...]}.\n'
        "The issues array must keep issue ids stable and remain dependency-valid.\n"
        "Each issue object should keep keys:\n"
        "- id, repo, title, description\n"
        "- optional: priority, depends_on, blockers\n\n"
        "Reviewer output:\n"
        "```json\n"
        f"{_truncate_payload(recommendations, max_len=12000)}\n"
        "```\n\n"
        "Current plan:\n"
        "```markdown\n"
        f"{_truncate_payload(plan_markdown, max_len=8000)}\n"
        "```\n\n"
        "Current ISSUES.json:\n"
        "```json\n"
        f"{_truncate_payload(original_issues_json, max_len=12000)}\n"
        "```"
    )


async def _review_and_update_issues(
    *,
    session: PlanningSession,
    plan_markdown: str,
    issues_json: str,
    settings: Settings,
) -> tuple[str, dict[str, Any]]:
    t0 = time.monotonic()

    claude_api_key = resolve_claude_api_key(settings)

    review_prompt = _format_review_prompt(
        session=session,
        plan_markdown=plan_markdown,
        issues_json=issues_json,
    )
    review_response = await call_claude(
        prompt=review_prompt,
        model=settings.planning_review_claude_model,
        api_key=claude_api_key,
        max_tokens=settings.planning_review_claude_max_tokens,
        trace_name="planning_review_claude",
        metadata={
            "session_id": session.id,
            "project_slug": session.project_slug,
        },
    )
    review_payload_raw = _extract_json_payload(review_response)
    if not isinstance(review_payload_raw, dict):
        raise ValueError("❌ ERROR: review response must be a JSON object")
    review_payload = review_payload_raw

    openai_api_key = resolve_openai_api_key(settings)

    revision_prompt = _format_revision_prompt(
        session=session,
        plan_markdown=plan_markdown,
        original_issues_json=issues_json,
        review_payload=review_payload,
    )
    revised_response = await call_openai(
        prompt=revision_prompt,
        model=settings.planning_review_openai_model,
        api_key=openai_api_key,
        max_tokens=settings.planning_review_openai_max_tokens,
        trace_name="planning_review_openai",
        metadata={
            "session_id": session.id,
            "project_slug": session.project_slug,
        },
    )
    revised_payload_raw = _extract_json_payload(revised_response)
    if not isinstance(revised_payload_raw, dict) or "issues" not in revised_payload_raw:
        raise ValueError("❌ ERROR: revision response must include an issues object")

    revised_issues_json = json.dumps(revised_payload_raw)
    parse_issues_payload(revised_issues_json)

    plan_recommendations = review_payload.get("plan_recommendations", [])
    issue_recommendations = review_payload.get("issue_recommendations", [])
    if not isinstance(plan_recommendations, list):
        plan_recommendations = []
    if not isinstance(issue_recommendations, list):
        issue_recommendations = []

    elapsed_s = round(time.monotonic() - t0, 2)

    return revised_issues_json, {
        "review_enabled": True,
        "plan_recommendations_count": len(plan_recommendations),
        "issue_recommendations_count": len(issue_recommendations),
        "reviewer_model": settings.planning_review_claude_model,
        "revision_model": settings.planning_review_openai_model,
        "overall_feedback": str(review_payload.get("overall_feedback", "")),
        "elapsed_seconds": elapsed_s,
    }


def _resolve_planner_pipeline() -> Any:
    return _planner_pipeline or _default_plan_pipeline


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _running_status_for_mode(mode_value: str) -> str:
    return f"{PLANNING_STATUS_RUNNING}_{mode_value}"


def _parse_issue_repo(repo: str) -> tuple[str, str]:
    """Split an `owner/name` issue repository reference."""
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise ValueError(f"❌ ERROR: invalid issue repo '{repo}', expected owner/repo")
    return owner.strip(), name.strip()


async def _cleanup_sessions() -> None:
    global _last_cleanup_time
    now_ts = _utc_now().timestamp()
    if now_ts - _last_cleanup_time < _CLEANUP_DEBOUNCE_SECONDS:
        return
    _last_cleanup_time = now_ts
    store = _resolve_store()
    await store.sweep_expired_sessions(
        now=_utc_now(),
        intake_ttl_hours=_PLANNING_INTAKE_TTL_HOURS,
        running_ttl_hours=_PLANNING_RUNNING_TTL_HOURS,
    )


async def _ensure_session(session_id: str) -> PlanningSession:
    store = _resolve_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"❌ ERROR: planning session not found (session_id={session_id})",
        )
    return session


def _log_planning_lifecycle_event(
    session: PlanningSession,
    *,
    stage: str,
    phase: str,
    resolution: str | None = None,
    **fields: Any,
) -> None:
    try:
        context = build_planning_lifecycle_context(
            session_id=session.id,
            project_slug=session.project_slug,
            phase=phase,
            mode=session.mode.value,
        )
    except Exception:
        return
    log_planning_lifecycle_event(
        logger,
        stage,
        context,
        resolution=resolution,
        **fields,
    )


async def _refresh_session_state(session: PlanningSession) -> PlanningSession:
    store = _resolve_store()
    questions = session.questions
    if session.status == PLANNING_STATUS_INTAKE_PENDING and intake_complete(
        questions,
        session.answers,
    ):
        session.status = PLANNING_STATUS_READY_TO_RUN
        session.updated_at = _utc_now()
        _log_planning_lifecycle_event(
            session,
            stage=STAGE_PLANNING_INTAKE,
            phase=PLANNING_PHASE_INTAKE,
            event_type="intake_complete",
            status=PLANNING_STATUS_READY_TO_RUN,
        )
        await store.append_event(
            session.id,
            PlanningEvent(
                session_id=session.id,
                event_type="intake_complete",
                payload={"status": session.status},
            ),
        )
        await store.update_session(session)
    return session


@planning_router.post("/sessions", status_code=201, response_model=PlanningSession)
async def create_planning_session(payload: PlanningSessionCreateRequest) -> PlanningSession:
    """Create a new planning session and generate intake questions."""
    _require_planner()
    await _cleanup_sessions()
    questions = generate_intake_questions(
        payload.request_text,
        mode=payload.mode,
        project_slug=payload.project_slug,
        issue_creation_enabled=False,
    )
    session = PlanningSession(
        project_slug=payload.project_slug,
        mode=payload.mode,
        request_text=payload.request_text,
        status=PLANNING_STATUS_INTAKE_PENDING,
        questions=questions,
        answers={},
    )
    for question in session.questions:
        question.session_id = session.id
    store = _resolve_store()
    await store.create_session(session)
    _log_planning_lifecycle_event(
        session,
        stage=STAGE_PLANNING_INTAKE,
        phase=PLANNING_PHASE_INTAKE,
        event_type="session_created",
        status=PLANNING_STATUS_INTAKE_PENDING,
        question_count=len(questions),
    )
    await store.append_event(
        session.id,
        PlanningEvent(
            session_id=session.id,
            event_type="session_created",
            payload={
                "status": PLANNING_STATUS_INTAKE_PENDING,
                "question_count": len(questions),
            },
        ),
    )
    return session


@planning_router.post(
    "/sessions/{session_id}/messages",
    status_code=200,
    response_model=PlanningMessage,
)
async def add_planning_message(
    session_id: str,
    payload: PlanningMessageCreate,
) -> PlanningMessage:
    """Record an intake answer and advance state when required questions are answered."""
    _require_planner()
    await _cleanup_sessions()
    session = await _ensure_session(session_id)
    _TERMINAL_STATUSES = (
        PLANNING_STATUS_DONE,
        PLANNING_STATUS_EXPIRED,
        PLANNING_STATUS_FAILED,
        PLANNING_STATUS_TIMED_OUT,
    )
    if session.status.startswith(PLANNING_STATUS_RUNNING):
        raise HTTPException(
            status_code=409,
            detail="❌ ERROR: session is already running and does not accept intake answers",
        )
    if session.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"❌ ERROR: session is {session.status} and does not accept intake answers",
        )

    questions = session.questions
    valid_ids = {question.id for question in questions}
    if payload.question_id not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"❌ ERROR: unknown question_id '{payload.question_id}'",
        )
    answer = payload.answer.strip()
    if not answer:
        raise HTTPException(status_code=400, detail="❌ ERROR: answer cannot be empty")

    message = PlanningMessage(
        session_id=session_id,
        source=payload.source,
        content=answer,
    )
    store = _resolve_store()
    await store.add_message(session_id, message)
    _log_planning_lifecycle_event(
        session,
        stage=STAGE_PLANNING_INTAKE,
        phase=PLANNING_PHASE_INTAKE,
        event_type="intake_answered",
        question_id=payload.question_id,
        status=session.status,
    )
    session.answers[payload.question_id] = answer
    await store.append_event(
        session_id,
        PlanningEvent(
            session_id=session_id,
            event_type="intake_answered",
            payload={
                "question_id": payload.question_id,
                "status": session.status,
            },
        ),
    )
    session = await _refresh_session_state(session)
    await store.update_session(session)
    return message


@planning_router.post(
    "/sessions/{session_id}:start",
    status_code=200,
    response_model=PlanningSession,
)
async def start_planning(session_id: str) -> PlanningSession:
    """Start planning execution for a prepared session."""
    _require_planner()
    await _cleanup_sessions()
    session = await _ensure_session(session_id)
    session = await _refresh_session_state(session)
    if session.status != PLANNING_STATUS_READY_TO_RUN:
        raise HTTPException(
            status_code=409,
            detail=(
                f"❌ ERROR: session cannot start until intake is complete (status={session.status})"
            ),
        )
    queued_at = _utc_now().isoformat()
    planner_queue = _resolve_planner_queue()
    store = _resolve_store()
    try:
        message_id = await planner_queue.publish(
            session_id=session_id,
            project_slug=session.project_slug,
            mode=session.mode.value,
            created_at=queued_at,
        )
    except Exception as exc:
        _log_planning_lifecycle_event(
            session,
            stage=STAGE_PLANNING_FAILED,
            phase=PLANNING_PHASE_INTAKE,
            event_type="failed_to_queue",
            status=session.status,
            error=str(exc),
        )
        await store.append_event(
            session_id,
            PlanningEvent(
                session_id=session_id,
                event_type="failed",
                payload={
                    "status": session.status,
                    "error": str(exc),
                },
            ),
        )
        raise HTTPException(
            status_code=500,
            detail=f"❌ ERROR: failed to queue planning job: {exc}",
        ) from exc

    session.status = PLANNING_STATUS_RUNNING
    session.updated_at = _utc_now()
    _log_planning_lifecycle_event(
        session,
        stage=STAGE_PLANNING_SCOUTING,
        phase=PLANNING_PHASE_SCOUTING,
        event_type="queued",
        status=PLANNING_STATUS_RUNNING,
        message_id=message_id,
        mode=session.mode.value,
    )
    await store.append_event(
        session_id,
        PlanningEvent(
            session_id=session_id,
            event_type="queued",
            payload={
                "status": PLANNING_STATUS_RUNNING,
                "message_id": message_id,
            },
        ),
    )
    await store.append_event(
        session_id,
        PlanningEvent(
            session_id=session_id,
            event_type="running",
            payload={"status": PLANNING_STATUS_RUNNING},
        ),
    )
    _log_planning_lifecycle_event(
        session,
        stage=STAGE_PLANNING_SCOUTING,
        phase=PLANNING_PHASE_SCOUTING,
        event_type="running",
        status=PLANNING_STATUS_RUNNING,
        mode=session.mode.value,
    )
    await store.update_session(session)
    return session


@planning_router.post(
    "/sessions/{session_id}/issues/approve",
    status_code=200,
    response_model=PlanningIssueApprovalResponse,
)
async def approve_session_issues(
    session_id: str,
    payload: PlanningIssueApprovalRequest,
) -> PlanningIssueApprovalResponse:
    """Move selected planning issues to the ready project status."""
    _require_planner()
    await _cleanup_sessions()
    if not payload.issues:
        raise HTTPException(status_code=400, detail="❌ ERROR: no issues provided")

    session = await _ensure_session(session_id)
    settings = get_settings()
    github_token = (settings.github_token or "").strip()
    if not github_token:
        raise HTTPException(
            status_code=500,
            detail="❌ ERROR: GitHub token is required to approve planning issues",
        )

    store = _resolve_store()
    ready_status = (settings.github_ready_status or "Ready").strip() or "Ready"
    approved: list[PlanningIssueApprovalSuccess] = []
    failed: list[PlanningIssueApprovalFailure] = []
    seen: set[tuple[str, str, int]] = set()

    api_client = GitHubAPIClient(github_token)
    try:
        projects_client = ProjectsV2Client(api_client)
        issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
        for issue in payload.issues:
            try:
                if issue.number <= 0:
                    raise ValueError(f"❌ ERROR: issue number must be positive, got {issue.number}")
                issue_repo = issue.repo.strip()
                if not issue_repo:
                    raise ValueError("❌ ERROR: issue repo is required")
                owner, name = _parse_issue_repo(issue_repo)
            except ValueError as exc:
                failed.append(
                    PlanningIssueApprovalFailure(
                        issue_id=issue.issue_id,
                        repo=issue.repo,
                        number=issue.number,
                        error=str(exc),
                    )
                )
                continue

            key = (owner, name, issue.number)
            if key in seen:
                continue
            seen.add(key)

            try:
                await issue_queue.set_project_status(
                    issue.number,
                    ready_status,
                    settings.github_project_name,
                    repo_owner=owner,
                    repo_name=name,
                )
                approved.append(
                    PlanningIssueApprovalSuccess(
                        issue_id=issue.issue_id,
                        repo=issue_repo,
                        number=issue.number,
                    )
                )
            except Exception as exc:
                failed.append(
                    PlanningIssueApprovalFailure(
                        issue_id=issue.issue_id,
                        repo=issue_repo,
                        number=issue.number,
                        error=str(exc),
                    )
                )
    finally:
        await api_client.close()

    await store.append_event(
        session_id,
        PlanningEvent(
            session_id=session_id,
            event_type="issues_approved",
            payload={
                "requested_count": len(payload.issues),
                "approved_count": len(approved),
                "failed_count": len(failed),
            },
        ),
    )

    _log_planning_lifecycle_event(
        session,
        stage=STAGE_PLANNING_ISSUE_WRITER,
        phase=PLANNING_PHASE_ISSUES,
        event_type="issues_approved",
        requested_count=len(payload.issues),
        approved_count=len(approved),
        failed_count=len(failed),
    )
    return PlanningIssueApprovalResponse(
        session_id=session_id,
        requested_count=len(payload.issues),
        approved_count=len(approved),
        failed_count=len(failed),
        approved=approved,
        failed=failed,
    )


@planning_worker_router.post("/internal/pubsub/planner")
async def run_planner_worker(request: Request) -> dict[str, Any]:
    """Handle planning jobs from Pub/Sub push subscriptions."""
    _require_planner_worker()
    await _cleanup_sessions()

    try:
        body = await request.json()
        queued = decode_pubsub_push(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{exc}") from exc

    if queued.event != "planner.start":
        raise HTTPException(
            status_code=400,
            detail=f"❌ ERROR: unsupported planner event '{queued.event}'",
        )

    store = _resolve_store()
    session = await _ensure_session(queued.payload.session_id)
    running_status = _running_status_for_mode(queued.payload.mode.value)
    session.status = running_status
    session.updated_at = _utc_now()

    try:
        planner_pipeline = _resolve_planner_pipeline()
        _log_planning_lifecycle_event(
            session,
            stage=STAGE_PLANNING_SCOUTING,
            phase=PLANNING_PHASE_SCOUTING,
            event_type="running",
            status=running_status,
            message_id=queued.message_id,
            mode=queued.payload.mode.value,
        )
        await store.append_event(
            session.id,
            PlanningEvent(
                session_id=session.id,
                event_type="running",
                payload={
                    "status": running_status,
                    "message_id": queued.message_id,
                    "mode": queued.payload.mode.value,
                },
            ),
        )
        await store.update_session(session)
        pipeline_output = await planner_pipeline(session)
        artifact_rows, artifact_payloads = _coerce_pipeline_output(pipeline_output)
        persisted_artifacts: list[PlanningArtifact] = []
        issues_created: list[dict[str, Any]] = []
        for artifact_type, content_url in artifact_rows:
            _log_planning_lifecycle_event(
                session,
                stage=STAGE_PLANNING_SYNTHESIS,
                phase=PLANNING_PHASE_SYNTHESIS,
                event_type="artifact_writing",
                artifact_type=artifact_type.value,
            )
            artifact = PlanningArtifact(
                session_id=session.id,
                artifact_type=artifact_type,
                content_url=content_url,
            )
            await store.add_artifact(session.id, artifact)
            persisted_artifacts.append(artifact)
            _log_planning_lifecycle_event(
                session,
                stage=STAGE_PLANNING_SYNTHESIS,
                phase=PLANNING_PHASE_SYNTHESIS,
                event_type="artifact_written",
                artifact_type=artifact_type.value,
                artifact_id=artifact.id,
                artifact_url=content_url,
            )
        if session.mode == PlanningMode.PLAN_AND_CREATE_ISSUES:
            issues_json = artifact_payloads.get(
                PlanningArtifactType.ISSUES_JSON,
                '{"issues": []}',
            )
            plan_markdown = artifact_payloads.get(
                PlanningArtifactType.PLAN_MARKDOWN,
                "",
            )
            settings = get_settings()
            reviewed_issues_json = issues_json
            review_summary: dict[str, Any] = {"review_enabled": False}
            if settings.planning_review_enabled:
                _log_planning_lifecycle_event(
                    session,
                    stage=STAGE_PLANNING_REVIEW,
                    phase=PLANNING_PHASE_REVIEW,
                    event_type="issues_review_started",
                    message_id=queued.message_id,
                )
                try:
                    reviewed_issues_json, review_summary = await _review_and_update_issues(
                        session=session,
                        plan_markdown=plan_markdown,
                        issues_json=issues_json,
                        settings=settings,
                    )
                    _log_planning_lifecycle_event(
                        session,
                        stage=STAGE_PLANNING_REVIEW,
                        phase=PLANNING_PHASE_REVIEW,
                        event_type="issues_review_complete",
                        message_id=queued.message_id,
                        review_plan_recommendations=review_summary.get(
                            "plan_recommendations_count",
                        ),
                        review_issue_recommendations=review_summary.get(
                            "issue_recommendations_count",
                        ),
                        review_overall_feedback=str(
                            review_summary.get("overall_feedback", ""),
                        ).strip(),
                        review_elapsed_seconds=review_summary.get(
                            "elapsed_seconds",
                        ),
                    )
                    review_artifact_content = json.dumps(review_summary, indent=2)
                    artifact_store = _resolve_artifact_store()
                    review_url = await artifact_store.write_artifact(
                        session_id=session.id,
                        filename="REVIEW.json",
                        content=review_artifact_content,
                        content_type="application/json",
                    )
                    review_artifact = PlanningArtifact(
                        session_id=session.id,
                        artifact_type=PlanningArtifactType.REVIEW_JSON,
                        content_url=review_url,
                    )
                    await store.add_artifact(session.id, review_artifact)
                    persisted_artifacts.append(review_artifact)
                except Exception as exc:
                    reviewed_issues_json = issues_json
                    review_summary = {
                        "review_enabled": True,
                        "status": "degraded",
                        "error": str(exc),
                    }
                    _log_planning_lifecycle_event(
                        session,
                        stage=STAGE_PLANNING_REVIEW,
                        phase=PLANNING_PHASE_REVIEW,
                        event_type="issues_review_failed",
                        message_id=queued.message_id,
                        error=str(exc),
                    )
                    await store.append_event(
                        session.id,
                        PlanningEvent(
                            session_id=session.id,
                            event_type="issues_review_failed",
                            payload={
                                "status": "failed",
                                "error": str(exc),
                                "mode": session.mode.value,
                            },
                        ),
                    )
            _log_planning_lifecycle_event(
                session,
                stage=STAGE_PLANNING_ISSUE_WRITER,
                phase=PLANNING_PHASE_ISSUES,
                event_type="issue_writer_started",
                message_id=queued.message_id,
            )
            if reviewed_issues_json != issues_json:
                await store.append_event(
                    session.id,
                    PlanningEvent(
                        session_id=session.id,
                        event_type="issues_revised",
                        payload={
                            "plan_recommendations_count": review_summary.get(
                                "plan_recommendations_count",
                                0,
                            ),
                            "issue_recommendations_count": review_summary.get(
                                "issue_recommendations_count",
                                0,
                            ),
                            "reviewer_model": review_summary.get("reviewer_model"),
                            "revision_model": review_summary.get("revision_model"),
                        },
                    ),
                )
            created_issues = await write_issues_from_payload(
                session=session,
                issues_json=reviewed_issues_json,
                project_slug=session.project_slug,
                github_token=(get_settings().github_token or ""),
            )
            for issue in created_issues:
                issue_payload = {
                    "issue_id": issue.issue_id,
                    "title": issue.title,
                    "url": issue.url,
                    "repo": issue.repo,
                    "number": issue.number,
                }
                issues_created.append(issue_payload)
                _log_planning_lifecycle_event(
                    session,
                    stage=STAGE_PLANNING_ISSUE_WRITER,
                    phase=PLANNING_PHASE_ISSUES,
                    event_type="issue_created",
                    created_issue_id=issue.issue_id,
                    created_issue_url=issue.url,
                    created_issue_number=issue.number,
                    created_issue_repo=issue.repo,
                )
            _log_planning_lifecycle_event(
                session,
                stage=STAGE_PLANNING_ISSUE_WRITER,
                phase=PLANNING_PHASE_ISSUES,
                event_type="issue_writer_done",
                requested_count=len(issues_created),
                created_count=len(issues_created),
                message_id=queued.message_id,
            )
            await store.append_event(
                session.id,
                PlanningEvent(
                    session_id=session.id,
                    event_type="issues_written",
                    payload={
                        "requested_count": len(issues_created),
                        "created": issues_created,
                    },
                ),
            )
        session.status = PLANNING_STATUS_DONE
        session.updated_at = _utc_now()
        _log_planning_lifecycle_event(
            session,
            stage=STAGE_PLANNING_DONE,
            phase=PLANNING_PHASE_DONE,
            event_type="done",
            status=PLANNING_STATUS_DONE,
            artifact_count=len(persisted_artifacts),
            issue_created_count=len(issues_created),
            message_id=queued.message_id,
        )
        await store.append_event(
            session.id,
            PlanningEvent(
                session_id=session.id,
                event_type="done",
                payload={
                    "status": PLANNING_STATUS_DONE,
                    "artifacts": [artifact.id for artifact in persisted_artifacts],
                    "issue_created_count": len(issues_created),
                },
            ),
        )
        await store.update_session(session)
        response_artifacts = [artifact.content_url for artifact in persisted_artifacts]
    except Exception as exc:
        session.status = PLANNING_STATUS_FAILED
        session.updated_at = _utc_now()
        try:
            _log_planning_lifecycle_event(
                session,
                stage=STAGE_PLANNING_FAILED,
                phase=PLANNING_PHASE_SYNTHESIS,
                event_type="failed",
                status=session.status,
                error=str(exc),
                message_id=queued.message_id,
                mode=queued.payload.mode.value,
            )
            await store.append_event(
                session.id,
                PlanningEvent(
                    session_id=session.id,
                    event_type="failed",
                    payload={
                        "status": session.status,
                        "error": str(exc),
                        "message_id": queued.message_id,
                    },
                ),
            )
            await store.update_session(session)
        except Exception:
            pass
        if isinstance(exc, PlanningArtifactStoreError):
            raise HTTPException(
                status_code=500,
                detail=f"❌ ERROR: planning artifact write failed: {exc}",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"❌ ERROR: planning worker failed: {exc}",
        ) from exc

    first_artifact = response_artifacts[0] if response_artifacts else ""
    return {
        "status": PLANNING_STATUS_DONE,
        "session_id": session.id,
        "artifact_urls": response_artifacts,
        "artifact_url": first_artifact,
        "message_id": queued.message_id,
    }


@planning_router.get(
    "/sessions/{session_id}",
    status_code=200,
    response_model=PlanningSession,
)
async def get_planning_session(session_id: str) -> PlanningSession:
    """Fetch session details and current question/answer state."""
    _require_planner()
    await _cleanup_sessions()
    session = await _ensure_session(session_id)
    return await _refresh_session_state(session)


@planning_router.get(
    "/sessions/{session_id}/events",
    status_code=200,
    response_model=PlanningEventList,
)
async def get_planning_events(
    session_id: str,
    after: str | None = Query(default=None, description="Pagination cursor"),
) -> PlanningEventList:
    """Read append-only session events, optionally after a cursor id."""
    _require_planner()
    await _cleanup_sessions()
    await _ensure_session(session_id)
    if after is not None and not after.strip():
        raise HTTPException(status_code=400, detail="❌ ERROR: after cursor must not be empty")

    store = _resolve_store()
    try:
        events, next_cursor = await store.get_events(session_id=session_id, after=after)
    except PlanningStoreError as exc:
        message = str(exc)
        if "cursor not found" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc

    return PlanningEventList(events=events, next_cursor=next_cursor)


@planning_router.get(
    "/sessions/{session_id}/artifacts",
    status_code=200,
    response_model=PlanningArtifactList,
)
async def get_planning_artifacts(session_id: str) -> PlanningArtifactList:
    """List planning artifacts for a session."""
    _require_planner()
    await _cleanup_sessions()
    await _ensure_session(session_id)
    store = _resolve_store()
    try:
        artifacts = await store.get_artifacts(session_id)
    except PlanningStoreError as exc:
        raise HTTPException(status_code=500, detail=f"{exc}") from exc
    return PlanningArtifactList(artifacts=artifacts)
