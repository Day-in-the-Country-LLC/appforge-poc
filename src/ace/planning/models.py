"""Shared planning API models and enums."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PlanningMode(str, Enum):
    """Planning execution mode."""

    PLAN_ONLY = "plan_only"


class PlanningArtifactType(str, Enum):
    """Artifact kinds produced by planning sessions."""

    PLAN_MARKDOWN = "plan_md"
    ISSUES_JSON = "issues_json"
    DEPENDENCIES_MMD = "dependencies_mmd"


def _uuid() -> str:
    return uuid4().hex


class PlanningSession(BaseModel):
    """Session metadata returned by planning endpoints."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "plan-session-01",
                    "project_slug": "example-project",
                    "mode": "plan_only",
                    "request_text": "Prepare a release plan for checkout.",
                    "status": "intake_pending",
                    "created_at": "2026-02-15T00:00:00Z",
                    "updated_at": "2026-02-15T00:00:00Z",
                }
            ]
        }
    )

    id: str = Field(default_factory=_uuid)
    project_slug: str
    mode: PlanningMode = PlanningMode.PLAN_ONLY
    request_text: str
    status: str = "intake_pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningSessionCreateRequest(BaseModel):
    """Request body for creating a planning session."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "project_slug": "example-project",
                    "mode": "plan_only",
                    "request_text": "Create a rollout plan for checkout checkout feature.",
                }
            ]
        }
    )

    project_slug: str
    mode: PlanningMode = PlanningMode.PLAN_ONLY
    request_text: str


class PlanningMessageCreate(BaseModel):
    """Payload for posting a message to an active planning session."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "source": "user",
                    "content": "Primary objective: launch checkout with mobile flow improvements.",
                }
            ]
        }
    )

    source: str = "user"
    content: str


class PlanningMessage(BaseModel):
    """A message in a planning session conversation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "msg-001",
                    "session_id": "plan-session-01",
                    "source": "user",
                    "content": "Feature work for checkout checkout route + API changes.",
                    "created_at": "2026-02-15T00:01:00Z",
                }
            ]
        }
    )

    id: str = Field(default_factory=_uuid)
    session_id: str
    source: str = "user"
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningQuestion(BaseModel):
    """An intake question surfaced to the user."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "q-001",
                    "session_id": "plan-session-01",
                    "text": "What is the primary goal category?",
                    "question_type": "single_choice",
                    "required": True,
                    "options": ["feature", "bugfix", "infra", "docs"],
                }
            ]
        }
    )

    id: str = Field(default_factory=_uuid)
    session_id: str
    text: str
    question_type: str = "text"
    required: bool = True
    options: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningEvent(BaseModel):
    """An event in the planning event stream."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "evt-001",
                    "session_id": "plan-session-01",
                    "event_type": "session_created",
                    "payload": {"status": "intake_pending"},
                    "created_at": "2026-02-15T00:00:10Z",
                }
            ]
        }
    )

    id: str = Field(default_factory=_uuid)
    session_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningArtifact(BaseModel):
    """Reference to a planning artifact stored in object storage."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "artifact-001",
                    "session_id": "plan-session-01",
                    "artifact_type": "plan_md",
                    "content_url": "https://storage.googleapis.com/bucket/plans/plan-session-01/PLAN.md",
                    "created_at": "2026-02-15T00:05:00Z",
                    "updated_at": "2026-02-15T00:05:00Z",
                }
            ]
        }
    )

    id: str = Field(default_factory=_uuid)
    session_id: str
    artifact_type: PlanningArtifactType
    content_url: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningEventList(BaseModel):
    """Paged list of planning events."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "events": [
                        {
                            "id": "evt-001",
                            "session_id": "plan-session-01",
                            "event_type": "session_created",
                            "payload": {"status": "intake_pending"},
                            "created_at": "2026-02-15T00:00:10Z",
                        }
                    ],
                    "next_cursor": "evt-001",
                }
            ]
        }
    )

    events: list[PlanningEvent]
    next_cursor: str | None = None


class PlanningArtifactList(BaseModel):
    """List of planning artifacts."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "artifacts": [
                        {
                            "id": "artifact-001",
                            "session_id": "plan-session-01",
                            "artifact_type": "plan_md",
                            "content_url": "https://storage.googleapis.com/bucket/plans/plan-session-01/PLAN.md",
                            "created_at": "2026-02-15T00:05:00Z",
                            "updated_at": "2026-02-15T00:05:00Z",
                        }
                    ]
                }
            ]
        }
    )

    artifacts: list[PlanningArtifact]
