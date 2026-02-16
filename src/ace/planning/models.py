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
    PLAN_AND_CREATE_ISSUES = "plan_and_create_issues"


class PlanningArtifactType(str, Enum):
    """Artifact kinds produced by planning sessions."""

    PLAN_MARKDOWN = "plan_md"
    ISSUES_JSON = "issues_json"
    DEPENDENCIES_MMD = "dependencies_mmd"
    REVIEW_JSON = "review_json"


class PlanningProjectRepository(BaseModel):
    """Single repository declaration in a planning project registry."""

    owner: str
    name: str
    local_path: str | None = None
    github_url: str | None = None


class PlanningProjectRegistry(BaseModel):
    """Project metadata used for scout planning."""

    project_slug: str
    repos: list[PlanningProjectRepository]


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
    intake_state: dict[str, Any] = Field(default_factory=dict)
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
                    "content": "We need a feature plan for checkout rollout.",
                },
            ]
        }
    )

    source: str = "user"
    content: str | None = None


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


class PlanningMessageList(BaseModel):
    """List of conversation messages for a planning session."""

    messages: list[PlanningMessage]


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


class PlanningIssueApprovalIssue(BaseModel):
    """Single issue reference for planning approval actions."""

    issue_id: str | None = None
    repo: str
    number: int
    title: str | None = None


class PlanningIssueApprovalRequest(BaseModel):
    """Batch approval request payload."""

    issues: list[PlanningIssueApprovalIssue]


class PlanningIssueApprovalSuccess(BaseModel):
    """A planning issue successfully approved."""

    issue_id: str | None = None
    repo: str
    number: int


class PlanningIssueApprovalFailure(BaseModel):
    """A planning issue that could not be approved."""

    issue_id: str | None = None
    repo: str
    number: int
    error: str


class PlanningIssueApprovalResponse(BaseModel):
    """Response from bulk approval of planning issues."""

    session_id: str
    requested_count: int
    approved_count: int
    failed_count: int
    approved: list[PlanningIssueApprovalSuccess]
    failed: list[PlanningIssueApprovalFailure]
