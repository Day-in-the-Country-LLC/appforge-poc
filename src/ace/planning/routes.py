"""HTTP API for planning sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from ace.config.settings import get_settings
from ace.planning.intake import generate_intake_questions, intake_complete
from ace.planning.models import (
    PlanningArtifactList,
    PlanningEvent,
    PlanningEventList,
    PlanningMessage,
    PlanningMessageCreate,
    PlanningQuestion,
    PlanningSession,
    PlanningSessionCreateRequest,
)

planning_router = APIRouter(prefix="/planning", tags=["planning"])

PLANNING_STATUS_INTAKE_PENDING = "intake_pending"
PLANNING_STATUS_READY_TO_RUN = "ready_to_run"
PLANNING_STATUS_RUNNING = "running"

_sessions: dict[str, PlanningSession] = {}
_session_messages: dict[str, list[PlanningMessage]] = {}
_session_events: dict[str, list[PlanningEvent]] = {}
_session_questions: dict[str, list[PlanningQuestion]] = {}
_session_answers: dict[str, dict[str, str]] = {}


def _planner_enabled() -> bool:
    role = (get_settings().webhook_service_role or "").strip().lower()
    return role in ("planner", "both", "all")


def _require_planner() -> None:
    if not _planner_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: planner endpoint disabled")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _append_event(session_id: str, event_type: str, payload: dict[str, object]) -> None:
    event = PlanningEvent(
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        created_at=_utc_now(),
    )
    _session_events.setdefault(session_id, []).append(event)


def _ensure_session(session_id: str) -> PlanningSession:
    if session_id not in _sessions:
        raise HTTPException(
            status_code=404,
            detail=f"❌ ERROR: planning session not found (session_id={session_id})",
        )
    return _sessions[session_id]


def _refresh_session_state(session: PlanningSession) -> PlanningSession:
    answers = _session_answers.get(session.id, {})
    questions = _session_questions.get(session.id, [])
    session.answers = dict(answers)
    session.questions = list(questions)
    if session.status == PLANNING_STATUS_INTAKE_PENDING and intake_complete(questions, answers):
        session.status = PLANNING_STATUS_READY_TO_RUN
        session.updated_at = _utc_now()
        _append_event(session.id, "intake_complete", {"status": session.status})
    return session


@planning_router.post("/sessions", status_code=201, response_model=PlanningSession)
async def create_planning_session(payload: PlanningSessionCreateRequest) -> PlanningSession:
    """Create a new planning session and generate intake questions."""
    _require_planner()
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
    _sessions[session.id] = session
    _session_messages[session.id] = []
    _session_questions[session.id] = questions
    _session_answers[session.id] = {}
    _session_events[session.id] = []
    _append_event(
        session.id,
        "session_created",
        {
            "status": PLANNING_STATUS_INTAKE_PENDING,
            "question_count": len(questions),
        },
    )
    return session


@planning_router.post("/sessions/{session_id}/messages", status_code=200, response_model=PlanningMessage)
async def add_planning_message(
    session_id: str,
    payload: PlanningMessageCreate,
) -> PlanningMessage:
    """Record an intake answer and advance state when required questions are answered."""
    _require_planner()
    session = _ensure_session(session_id)
    if session.status == PLANNING_STATUS_RUNNING:
        raise HTTPException(
            status_code=409,
            detail="❌ ERROR: session is already running and does not accept intake answers",
        )

    questions = _session_questions.get(session_id, [])
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
    _session_messages.setdefault(session_id, []).append(message)
    _session_answers.setdefault(session_id, {})[payload.question_id] = answer
    _append_event(
        session_id,
        "intake_answered",
        {
            "question_id": payload.question_id,
            "status": session.status,
        },
    )
    session.answers = dict(_session_answers[session_id])
    session = _refresh_session_state(session)
    _sessions[session_id] = session
    return message


@planning_router.post("/sessions/{session_id}:start", status_code=200, response_model=PlanningSession)
async def start_planning(session_id: str) -> PlanningSession:
    """Start planning execution for a prepared session."""
    _require_planner()
    session = _ensure_session(session_id)
    session = _refresh_session_state(session)
    if session.status != PLANNING_STATUS_READY_TO_RUN:
        raise HTTPException(
            status_code=409,
            detail=(
                "❌ ERROR: session cannot start until intake is complete "
                f"(status={session.status})"
            ),
        )
    session.status = PLANNING_STATUS_RUNNING
    session.updated_at = _utc_now()
    _append_event(session_id, "session_started", {"status": PLANNING_STATUS_RUNNING})
    _sessions[session_id] = session
    return session


@planning_router.get("/sessions/{session_id}", status_code=200, response_model=PlanningSession)
async def get_planning_session(session_id: str) -> PlanningSession:
    """Fetch session details and current question/answer state."""
    _require_planner()
    session = _ensure_session(session_id)
    return _refresh_session_state(session)


@planning_router.get("/sessions/{session_id}/events", status_code=200, response_model=PlanningEventList)
async def get_planning_events(
    session_id: str,
    after: str | None = Query(default=None, description="Pagination cursor"),
) -> PlanningEventList:
    """Read append-only session events, optionally after a cursor id."""
    _require_planner()
    _ensure_session(session_id)
    if after is not None and not after.strip():
        raise HTTPException(status_code=400, detail="❌ ERROR: after cursor must not be empty")

    events = _session_events.get(session_id, [])
    if after is None:
        return PlanningEventList(events=events, next_cursor=events[-1].id if events else None)

    cursor_index = None
    for index, event in enumerate(events):
        if event.id == after:
            cursor_index = index
            break
    if cursor_index is None:
        raise HTTPException(status_code=404, detail="❌ ERROR: cursor not found")
    next_events = events[cursor_index + 1 :]
    return PlanningEventList(
        events=next_events,
        next_cursor=next_events[-1].id if next_events else None,
    )


@planning_router.get("/sessions/{session_id}/artifacts", status_code=200, response_model=PlanningArtifactList)
async def get_planning_artifacts(session_id: str) -> PlanningArtifactList:
    """List placeholder artifacts for Step 3 (none yet produced)."""
    _require_planner()
    _ensure_session(session_id)
    return PlanningArtifactList(artifacts=[])
