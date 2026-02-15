"""HTTP API for planning sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ace.config.settings import get_settings
from ace.planning.artifacts import (
    GCSPlanningArtifactStore,
    PlanningArtifactStore,
    PlanningArtifactStoreError,
)
from ace.planning.intake import generate_intake_questions, intake_complete
from ace.planning.models import (
    PlanningArtifact,
    PlanningArtifactList,
    PlanningArtifactType,
    PlanningEvent,
    PlanningEventList,
    PlanningMessage,
    PlanningMessageCreate,
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
    PLANNING_STATUS_INTAKE_PENDING,
    PLANNING_STATUS_READY_TO_RUN,
    PLANNING_STATUS_RUNNING,
    PLANNING_STATUS_TIMED_OUT,
    PlanningStore,
    PlanningStoreError,
    build_planning_store,
)

planning_router = APIRouter(prefix="/planning", tags=["planning"])
planning_worker_router = APIRouter(tags=["planning"])

_PLANNING_INTAKE_TTL_HOURS = 24
_PLANNING_RUNNING_TTL_HOURS = 1
_store: PlanningStore | None = None
_planner_queue: PubSubPlannerQueue | None = None
_artifact_store: PlanningArtifactStore | None = None
_planner_pipeline: Any | None = None


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
) -> list[tuple[PlanningArtifactType, str]]:
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
    return [
        (PlanningArtifactType.PLAN_MARKDOWN, plan_url),
        (PlanningArtifactType.ISSUES_JSON, issues_url),
        (PlanningArtifactType.DEPENDENCIES_MMD, dependencies_url),
    ]


def _resolve_planner_pipeline() -> Any:
    return _planner_pipeline or _default_plan_pipeline


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _running_status_for_mode(mode_value: str) -> str:
    return f"{PLANNING_STATUS_RUNNING}_{mode_value}"


async def _cleanup_sessions() -> None:
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


async def _refresh_session_state(session: PlanningSession) -> PlanningSession:
    store = _resolve_store()
    questions = session.questions
    if session.status == PLANNING_STATUS_INTAKE_PENDING and intake_complete(
        questions,
        session.answers,
    ):
        session.status = PLANNING_STATUS_READY_TO_RUN
        session.updated_at = _utc_now()
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
    store = _resolve_store()
    await store.create_session(session)
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
    if (
        session.status.startswith(PLANNING_STATUS_RUNNING)
        or session.status == PLANNING_STATUS_TIMED_OUT
    ):
        raise HTTPException(
            status_code=409,
            detail="❌ ERROR: session is already running and does not accept intake answers",
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
                "❌ ERROR: session cannot start until intake is complete "
                f"(status={session.status})"
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
    await store.update_session(session)
    return session


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
        artifact_results = await planner_pipeline(session)
        persisted_artifacts: list[PlanningArtifact] = []
        for artifact_type, content_url in artifact_results:
            artifact = PlanningArtifact(
                session_id=session.id,
                artifact_type=artifact_type,
                content_url=content_url,
            )
            await store.add_artifact(session.id, artifact)
            persisted_artifacts.append(artifact)
        session.status = PLANNING_STATUS_DONE
        session.updated_at = _utc_now()
        await store.append_event(
            session.id,
            PlanningEvent(
                session_id=session.id,
                event_type="done",
                payload={
                    "status": PLANNING_STATUS_DONE,
                    "artifacts": [artifact.id for artifact in persisted_artifacts],
                },
            ),
        )
        await store.update_session(session)
        response_artifacts = [artifact.content_url for artifact in persisted_artifacts]
    except Exception as exc:
        session.status = "failed"
        session.updated_at = _utc_now()
        try:
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
