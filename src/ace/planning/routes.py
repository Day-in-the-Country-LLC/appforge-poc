"""HTTP API stubs for planning sessions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ace.config.settings import get_settings
from ace.planning.models import (
    PlanningArtifactList,
    PlanningEventList,
    PlanningMessage,
    PlanningMessageCreate,
    PlanningSession,
    PlanningSessionCreateRequest,
)


planning_router = APIRouter(prefix="/planning", tags=["planning"])


def _planner_enabled() -> bool:
    role = (get_settings().webhook_service_role or "").strip().lower()
    return role in ("planner", "both", "all")


def _require_planner() -> None:
    if not _planner_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: planner endpoint disabled")


@planning_router.post("/sessions", status_code=501, response_model=PlanningSession)
async def create_planning_session(
    payload: PlanningSessionCreateRequest,
) -> PlanningSession:
    """
    Create a new planning session.

    Example:
    {
      "project_slug": "example-project",
      "mode": "plan_only",
      "request_text": "Generate a cross-repo planning run for checkout launch."
    }
    """
    _require_planner()
    raise HTTPException(
        status_code=501,
        detail="❌ ERROR: planning session creation not yet implemented",
    )


@planning_router.post("/sessions/{session_id}/messages", status_code=501, response_model=PlanningMessage)
async def add_planning_message(
    session_id: str, payload: PlanningMessageCreate
) -> PlanningMessage:
    """
    Add an intake or follow-up answer to a planning session.
    """
    _require_planner()
    raise HTTPException(
        status_code=501,
        detail=f"❌ ERROR: planning message endpoint not yet implemented (session={session_id})",
    )


@planning_router.post("/sessions/{session_id}:start", status_code=501, response_model=PlanningSession)
async def start_planning(session_id: str) -> PlanningSession:
    """
    Start planning execution for a session.
    """
    _require_planner()
    raise HTTPException(
        status_code=501,
        detail=f"❌ ERROR: planning start endpoint not yet implemented (session={session_id})",
    )


@planning_router.get("/sessions/{session_id}", status_code=501, response_model=PlanningSession)
async def get_planning_session(session_id: str) -> PlanningSession:
    """
    Fetch planning session details.
    """
    _require_planner()
    raise HTTPException(
        status_code=501,
        detail=f"❌ ERROR: planning session read not yet implemented (session={session_id})",
    )


@planning_router.get(
    "/sessions/{session_id}/events", status_code=501, response_model=PlanningEventList
)
async def get_planning_events(
    session_id: str, after: str | None = Query(default=None, description="Pagination cursor")
) -> PlanningEventList:
    """
    Read session events from a cursor.
    """
    _require_planner()
    if after is not None and not after.strip():
        raise HTTPException(status_code=400, detail="❌ ERROR: after cursor must not be empty")
    raise HTTPException(
        status_code=501,
        detail=f"❌ ERROR: planning events endpoint not yet implemented (session={session_id})",
    )


@planning_router.get(
    "/sessions/{session_id}/artifacts",
    status_code=501,
    response_model=PlanningArtifactList,
)
async def get_planning_artifacts(session_id: str) -> PlanningArtifactList:
    """
    List artifacts generated for a planning session.
    """
    _require_planner()
    raise HTTPException(
        status_code=501,
        detail=f"❌ ERROR: planning artifacts endpoint not yet implemented (session={session_id})",
    )
