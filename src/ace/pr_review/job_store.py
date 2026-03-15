"""Persistence helpers for PR review job idempotency and session state."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ace.config.settings import Settings
from . import models

try:
    from google.api_core.exceptions import AlreadyExists  # type: ignore[import-untyped]
    from google.cloud import firestore  # type: ignore[import-untyped]
    from google.cloud.exceptions import Conflict  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    AlreadyExists = None
    Conflict = None
    firestore = None


PR_REVIEW_JOB_STATUS_QUEUED = "queued"
PR_REVIEW_JOB_STATUS_IN_PROGRESS = "in_progress"
PR_REVIEW_JOB_STATUS_CONSENSUS_APPROVE = "consensus_approve"
PR_REVIEW_JOB_STATUS_CONSENSUS_REJECT = "consensus_reject"
PR_REVIEW_JOB_STATUS_ERROR = "error"


class PRReviewJobStoreError(ValueError):
    """Domain error for PR review job persistence."""


@dataclass(frozen=True)
class PRReviewJobRecord:
    """Stored review job metadata keyed by repo, PR, and head SHA."""

    idempotency_key: str
    repo_owner: str
    repo_name: str
    pr_number: int
    head_sha: str
    base_ref: str
    action: str
    workflow_id: str
    installation_id: int
    status: str
    created_at: str
    updated_at: str
    delivery_id: str | None = None
    project: str | None = None
    target_gcp_project: str | None = None
    queued_at: str | None = None
    pubsub_message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
            "action": self.action,
            "workflow_id": self.workflow_id,
            "installation_id": self.installation_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "delivery_id": self.delivery_id,
            "project": self.project,
            "target_gcp_project": self.target_gcp_project,
            "queued_at": self.queued_at,
            "pubsub_message_id": self.pubsub_message_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PRReviewJobRecord":
        return cls(
            idempotency_key=_required_string(payload.get("idempotency_key"), "idempotency_key"),
            repo_owner=_required_string(payload.get("repo_owner"), "repo_owner"),
            repo_name=_required_string(payload.get("repo_name"), "repo_name"),
            pr_number=_required_int(payload.get("pr_number"), "pr_number"),
            head_sha=_required_string(payload.get("head_sha"), "head_sha"),
            base_ref=_required_string(payload.get("base_ref"), "base_ref"),
            action=_required_string(payload.get("action"), "action"),
            workflow_id=_required_string(payload.get("workflow_id"), "workflow_id"),
            installation_id=_required_int(payload.get("installation_id"), "installation_id"),
            status=_required_string(payload.get("status"), "status"),
            created_at=_required_string(payload.get("created_at"), "created_at"),
            updated_at=_required_string(payload.get("updated_at"), "updated_at"),
            delivery_id=_optional_string(payload.get("delivery_id"), "delivery_id"),
            project=_optional_string(payload.get("project"), "project"),
            target_gcp_project=_optional_string(
                payload.get("target_gcp_project"),
                "target_gcp_project",
            ),
            queued_at=_optional_string(payload.get("queued_at"), "queued_at"),
            pubsub_message_id=_optional_string(
                payload.get("pubsub_message_id"),
                "pubsub_message_id",
            ),
        )


@dataclass(frozen=True)
class PRReviewClaimResult:
    """Result of attempting to claim a review job idempotency key."""

    claimed: bool
    record: PRReviewJobRecord
    reason: str | None = None


class PRReviewJobStore(ABC):
    """Interface for durable PR review job idempotency."""

    @abstractmethod
    async def claim_job(self, record: PRReviewJobRecord) -> PRReviewClaimResult: ...

    @abstractmethod
    async def mark_enqueued(
        self,
        *,
        idempotency_key: str,
        pubsub_message_id: str,
        queued_at: str,
    ) -> None: ...

    @abstractmethod
    async def release_claim(self, idempotency_key: str) -> None: ...


class InMemoryPRReviewJobStore(PRReviewJobStore):
    """In-memory store used by tests."""

    def __init__(self) -> None:
        self._records: dict[str, PRReviewJobRecord] = {}

    async def claim_job(self, record: PRReviewJobRecord) -> PRReviewClaimResult:
        existing = self._records.get(record.idempotency_key)
        if existing is not None:
            return PRReviewClaimResult(
                claimed=False,
                record=existing,
                reason=_existing_claim_reason(existing.status),
            )
        self._records[record.idempotency_key] = record
        return PRReviewClaimResult(claimed=True, record=record)

    async def mark_enqueued(
        self,
        *,
        idempotency_key: str,
        pubsub_message_id: str,
        queued_at: str,
    ) -> None:
        existing = self._records.get(idempotency_key)
        if existing is None:
            raise PRReviewJobStoreError(
                f"❌ ERROR: PR review job not found (idempotency_key={idempotency_key})"
            )
        self._records[idempotency_key] = PRReviewJobRecord(
            **{
                **existing.to_dict(),
                "queued_at": queued_at,
                "pubsub_message_id": pubsub_message_id,
                "updated_at": _utc_now_iso(),
            }
        )

    async def release_claim(self, idempotency_key: str) -> None:
        self._records.pop(idempotency_key, None)


class FirestorePRReviewJobStore(PRReviewJobStore):
    """Firestore-backed PR review job store."""

    def __init__(self, *, db: Any, collection_name: str = "pr_review_jobs") -> None:
        self._db = db
        self._collection_name = collection_name

    @classmethod
    def from_settings(cls, settings: Settings) -> "FirestorePRReviewJobStore":
        project_id = (settings.gcp_project_id or "").strip()
        if not project_id:
            raise PRReviewJobStoreError(
                "❌ ERROR: GCP_PROJECT_ID is required for PR review job store"
            )
        if firestore is None:
            raise PRReviewJobStoreError(
                "❌ ERROR: google-cloud-firestore is required for PR review job store"
            )
        client = firestore.Client(project=project_id)
        return cls(db=client)

    def _record_ref(self, idempotency_key: str) -> Any:
        return self._db.collection(self._collection_name).document(idempotency_key)

    async def claim_job(self, record: PRReviewJobRecord) -> PRReviewClaimResult:
        ref = self._record_ref(record.idempotency_key)
        try:
            await asyncio.to_thread(ref.create, record.to_dict())
            return PRReviewClaimResult(claimed=True, record=record)
        except Exception as exc:
            duplicate_error = False
            if AlreadyExists is not None and isinstance(exc, AlreadyExists):
                duplicate_error = True
            if Conflict is not None and isinstance(exc, Conflict):
                duplicate_error = True
            if duplicate_error:
                snapshot = await asyncio.to_thread(ref.get)
                if not snapshot.exists:
                    raise PRReviewJobStoreError(
                        "❌ ERROR: PR review job claim conflicted but record is missing"
                    ) from exc
                existing = PRReviewJobRecord.from_dict(snapshot.to_dict() or {})
                return PRReviewClaimResult(
                    claimed=False,
                    record=existing,
                    reason=_existing_claim_reason(existing.status),
                )
            raise PRReviewJobStoreError(
                f"❌ ERROR: failed to claim PR review job ({record.idempotency_key}): {exc}"
            ) from exc

    async def mark_enqueued(
        self,
        *,
        idempotency_key: str,
        pubsub_message_id: str,
        queued_at: str,
    ) -> None:
        ref = self._record_ref(idempotency_key)
        snapshot = await asyncio.to_thread(ref.get)
        if not snapshot.exists:
            raise PRReviewJobStoreError(
                f"❌ ERROR: PR review job not found (idempotency_key={idempotency_key})"
            )
        await asyncio.to_thread(
            ref.update,
            {
                "queued_at": queued_at,
                "pubsub_message_id": pubsub_message_id,
                "updated_at": _utc_now_iso(),
            },
        )

    async def release_claim(self, idempotency_key: str) -> None:
        await asyncio.to_thread(self._record_ref(idempotency_key).delete)


def build_pr_review_job_store(settings: Settings) -> PRReviewJobStore:
    """Build the production PR review store from settings."""
    return FirestorePRReviewJobStore.from_settings(settings)


def build_pr_review_job_record(
    *,
    idempotency_key: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
    base_ref: str,
    action: str,
    workflow_id: str,
    installation_id: int,
    delivery_id: str | None,
    project: str | None,
    target_gcp_project: str | None,
) -> PRReviewJobRecord:
    timestamp = _utc_now_iso()
    return PRReviewJobRecord(
        idempotency_key=idempotency_key,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        head_sha=head_sha,
        base_ref=base_ref,
        action=action,
        workflow_id=workflow_id,
        installation_id=installation_id,
        status=PR_REVIEW_JOB_STATUS_QUEUED,
        created_at=timestamp,
        updated_at=timestamp,
        delivery_id=delivery_id,
        project=project,
        target_gcp_project=target_gcp_project,
    )


class PRReviewSessionStoreError(ValueError):
    """Domain error for PR review session persistence."""


class PRReviewSessionStore(ABC):
    """Interface for persisting PR review sessions."""

    @abstractmethod
    async def create_session(self, session: models.PRReviewSession) -> None: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> models.PRReviewSession | None: ...

    @abstractmethod
    async def update_session(self, session: models.PRReviewSession) -> None: ...


class InMemoryPRReviewSessionStore(PRReviewSessionStore):
    """In-memory store for PR review session state."""

    def __init__(self) -> None:
        self._sessions: dict[str, models.PRReviewSession] = {}

    async def create_session(self, session: models.PRReviewSession) -> None:
        if session.session_id in self._sessions:
            raise PRReviewSessionStoreError(
                f"❌ ERROR: PR review session already exists (session_id={session.session_id})"
            )
        self._sessions[session.session_id] = session

    async def get_session(self, session_id: str) -> models.PRReviewSession | None:
        return self._sessions.get(session_id)

    async def update_session(self, session: models.PRReviewSession) -> None:
        if session.session_id not in self._sessions:
            raise PRReviewSessionStoreError(
                f"❌ ERROR: PR review session not found (session_id={session.session_id})"
            )
        self._sessions[session.session_id] = session


class FirestorePRReviewSessionStore(PRReviewSessionStore):
    """Firestore-backed PR review session store."""

    def __init__(self, *, db: Any) -> None:
        self._db = db

    @classmethod
    def from_settings(cls, settings: Settings) -> "FirestorePRReviewSessionStore":
        project_id = (settings.gcp_project_id or "").strip()
        if not project_id:
            raise PRReviewSessionStoreError(
                "❌ ERROR: GCP_PROJECT_ID is required for PR review session store"
            )
        if firestore is None:
            raise PRReviewSessionStoreError(
                "❌ ERROR: google-cloud-firestore is required for PR review session store"
            )
        client = firestore.Client(project=project_id)
        return cls(db=client)

    def _session_ref(self, session_id: str) -> Any:
        return self._db.collection("pr_review_sessions").document(session_id)

    async def create_session(self, session: models.PRReviewSession) -> None:
        ref = self._session_ref(session.session_id)
        existing = await asyncio.to_thread(ref.get)
        if existing.exists:
            raise PRReviewSessionStoreError(
                f"❌ ERROR: PR review session already exists (session_id={session.session_id})"
            )
        await asyncio.to_thread(ref.set, session.to_dict())

    async def get_session(self, session_id: str) -> models.PRReviewSession | None:
        snapshot = await asyncio.to_thread(self._session_ref(session_id).get)
        if not snapshot.exists:
            return None
        return models.PRReviewSession.from_dict(snapshot.to_dict() or {})

    async def update_session(self, session: models.PRReviewSession) -> None:
        ref = self._session_ref(session.session_id)
        snapshot = await asyncio.to_thread(ref.get)
        if not snapshot.exists:
            raise PRReviewSessionStoreError(
                f"❌ ERROR: PR review session not found (session_id={session.session_id})"
            )
        await asyncio.to_thread(ref.set, session.to_dict())


def build_pr_review_session_store(settings: Settings) -> PRReviewSessionStore:
    """Build the production PR review session store from settings."""
    return FirestorePRReviewSessionStore.from_settings(settings)


def _existing_claim_reason(status: str) -> str:
    if status in {PR_REVIEW_JOB_STATUS_QUEUED, PR_REVIEW_JOB_STATUS_IN_PROGRESS}:
        return "review_in_progress"
    return "review_already_recorded"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _required_string(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PRReviewJobStoreError(f"❌ ERROR: {field_name} must be a non-empty string")


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PRReviewJobStoreError(
        f"❌ ERROR: {field_name} must be a non-empty string when provided"
    )


def _required_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PRReviewJobStoreError(f"❌ ERROR: {field_name} must be an integer") from exc
