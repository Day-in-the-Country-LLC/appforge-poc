"""Pydantic-free dataclass domain models for collaborative PR reviews."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ReviewVerdict(str, Enum):
    """High-level LLM verdict for a review payload."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    COMMENT = "comment"


class ReviewConfidence(str, Enum):
    """Confidence level associated with a review verdict."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PRReviewStatus(str, Enum):
    """Lifecycle states used by PR review sessions."""

    IN_PROGRESS = "in_progress"
    CONSENSUS_APPROVE = "consensus_approve"
    CONSENSUS_REJECT = "consensus_reject"
    ERROR = "error"


def _to_enum(enum_cls: type[Enum], value: Any, field_name: str) -> Enum:
    """Parse and validate enum payload values with explicit fail-fast errors."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except Exception as exc:
        raise ValueError(
            f"❌ ERROR: {field_name} must be one of "
            f"{[member.value for member in enum_cls]} (got {value!r})"
        ) from exc


def _iso_to_utc_datetime(value: Any, field_name: str) -> datetime:
    """Parse timestamps from ISO-8601 strings with fail-fast validation."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"❌ ERROR: {field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"❌ ERROR: {field_name} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class ReviewFinding:
    """Single finding produced by a model during PR review."""

    file_path: str
    line_start: int | None
    line_end: int | None
    severity: str
    category: str
    description: str
    suggested_fix: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe data."""
        return {
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewFinding":
        """Deserialize from a validated payload."""
        return cls(
            file_path=str(payload["file_path"]),
            line_start=payload.get("line_start"),
            line_end=payload.get("line_end"),
            severity=str(payload["severity"]),
            category=str(payload["category"]),
            description=str(payload["description"]),
            suggested_fix=payload.get("suggested_fix"),
        )


@dataclass
class ReviewerVerdict:
    """Verdict and findings produced by a single model reviewer."""

    reviewer_model: str
    verdict: ReviewVerdict
    confidence: ReviewConfidence
    blocking_findings: list[ReviewFinding] = field(default_factory=list)
    non_blocking_findings: list[ReviewFinding] = field(default_factory=list)
    suggested_comments: list[str] = field(default_factory=list)
    reasoning: str = ""
    round_number: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe data."""
        return {
            "reviewer_model": self.reviewer_model,
            "verdict": self.verdict.value,
            "confidence": self.confidence.value,
            "blocking_findings": [finding.to_dict() for finding in self.blocking_findings],
            "non_blocking_findings": [finding.to_dict() for finding in self.non_blocking_findings],
            "suggested_comments": list(self.suggested_comments),
            "reasoning": self.reasoning,
            "round_number": self.round_number,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewerVerdict":
        """Deserialize from a validated payload."""
        return cls(
            reviewer_model=str(payload["reviewer_model"]),
            verdict=_to_enum(ReviewVerdict, payload["verdict"], "verdict"),
            confidence=_to_enum(ReviewConfidence, payload["confidence"], "confidence"),
            blocking_findings=[
                ReviewFinding.from_dict(finding)
                for finding in payload.get("blocking_findings", [])
            ],
            non_blocking_findings=[
                ReviewFinding.from_dict(finding)
                for finding in payload.get("non_blocking_findings", [])
            ],
            suggested_comments=list(payload.get("suggested_comments", [])),
            reasoning=str(payload.get("reasoning", "")),
            round_number=int(payload.get("round_number", 1)),
        )


@dataclass
class PRReviewSession:
    """Aggregate state for one PR review collaboration session."""

    session_id: str
    pr_number: int
    repo_owner: str
    repo_name: str
    head_sha: str
    base_branch: str
    created_at: datetime
    updated_at: datetime
    status: PRReviewStatus
    rounds: list[dict[str, ReviewerVerdict]] = field(default_factory=list)
    final_codex_verdict: ReviewerVerdict | None = None
    final_claude_verdict: ReviewerVerdict | None = None
    merged: bool = False
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe data."""
        return {
            "session_id": self.session_id,
            "pr_number": self.pr_number,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "head_sha": self.head_sha,
            "base_branch": self.base_branch,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status.value,
            "rounds": [
                {reviewer: verdict.to_dict() for reviewer, verdict in item.items()}
                for item in self.rounds
            ],
            "final_codex_verdict": (
                self.final_codex_verdict.to_dict() if self.final_codex_verdict else None
            ),
            "final_claude_verdict": (
                self.final_claude_verdict.to_dict() if self.final_claude_verdict else None
            ),
            "merged": self.merged,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PRReviewSession":
        """Deserialize from a validated payload."""
        return cls(
            session_id=str(payload["session_id"]),
            pr_number=int(payload["pr_number"]),
            repo_owner=str(payload["repo_owner"]),
            repo_name=str(payload["repo_name"]),
            head_sha=str(payload["head_sha"]),
            base_branch=str(payload["base_branch"]),
            created_at=_iso_to_utc_datetime(payload["created_at"], "created_at"),
            updated_at=_iso_to_utc_datetime(payload["updated_at"], "updated_at"),
            status=_to_enum(PRReviewStatus, payload["status"], "status"),
            rounds=[
                {
                    str(reviewer): ReviewerVerdict.from_dict(verdict)
                    for reviewer, verdict in item.items()
                }
                for item in payload.get("rounds", [])
            ],
            final_codex_verdict=(
                ReviewerVerdict.from_dict(payload["final_codex_verdict"])
                if payload.get("final_codex_verdict")
                else None
            ),
            final_claude_verdict=(
                ReviewerVerdict.from_dict(payload["final_claude_verdict"])
                if payload.get("final_claude_verdict")
                else None
            ),
            merged=bool(payload.get("merged", False)),
            error_message=payload.get("error_message"),
        )
