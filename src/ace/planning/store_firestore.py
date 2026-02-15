"""Persistence backends for planning sessions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any

from ace.config.settings import Settings
from ace.planning.models import (
    PlanningArtifact,
    PlanningEvent,
    PlanningMessage,
    PlanningSession,
)

try:
    from google.cloud import firestore  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    firestore = None


class PlanningStoreError(ValueError):
    """Domain error for planning store operations."""


PLANNING_STATUS_INTAKE_PENDING = "intake_pending"
PLANNING_STATUS_READY_TO_RUN = "ready_to_run"
PLANNING_STATUS_RUNNING = "running"
PLANNING_STATUS_EXPIRED = "expired"
PLANNING_STATUS_TIMED_OUT = "timed_out"


class PlanningStore(ABC):
    """Interface used by planning routes."""

    @abstractmethod
    async def create_session(self, session: PlanningSession) -> None: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> PlanningSession | None: ...

    @abstractmethod
    async def update_session(self, session: PlanningSession) -> None: ...

    @abstractmethod
    async def add_message(self, session_id: str, message: PlanningMessage) -> None: ...

    @abstractmethod
    async def append_event(self, session_id: str, event: PlanningEvent) -> None: ...

    @abstractmethod
    async def get_events(
        self,
        session_id: str,
        after: str | None = None,
    ) -> tuple[list[PlanningEvent], str | None]: ...

    @abstractmethod
    async def get_artifacts(self, session_id: str) -> list[PlanningArtifact]: ...

    @abstractmethod
    async def sweep_expired_sessions(
        self,
        *,
        now: datetime,
        intake_ttl_hours: float = 24.0,
        running_ttl_hours: float = 1.0,
    ) -> int: ...


class InMemoryPlanningStore(PlanningStore):
    """In-memory implementation for local tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, PlanningSession] = {}
        self._messages: dict[str, list[PlanningMessage]] = {}
        self._events: dict[str, list[PlanningEvent]] = {}
        self._artifacts: dict[str, list[PlanningArtifact]] = {}

    async def create_session(self, session: PlanningSession) -> None:
        if session.id in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session already exists (session_id={session.id})"
            )
        self._sessions[session.id] = session.model_copy(deep=True)
        self._messages[session.id] = []
        self._events[session.id] = []
        self._artifacts[session.id] = []

    async def get_session(self, session_id: str) -> PlanningSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.model_copy(deep=True)

    async def update_session(self, session: PlanningSession) -> None:
        if session.id not in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session.id})"
            )
        self._sessions[session.id] = session.model_copy(deep=True)

    async def add_message(self, session_id: str, message: PlanningMessage) -> None:
        if session_id not in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        self._messages.setdefault(session_id, []).append(message.model_copy(deep=True))

    async def append_event(self, session_id: str, event: PlanningEvent) -> None:
        if session_id not in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        self._events.setdefault(session_id, []).append(event.model_copy(deep=True))

    async def get_events(
        self,
        session_id: str,
        after: str | None = None,
    ) -> tuple[list[PlanningEvent], str | None]:
        if session_id not in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        events = list(self._events.get(session_id, []))
        if after is None:
            return events, events[-1].id if events else None

        for index, event in enumerate(events):
            if event.id == after:
                events = events[index + 1 :]
                return events, events[-1].id if events else None
        raise PlanningStoreError("❌ ERROR: cursor not found")

    async def get_artifacts(self, session_id: str) -> list[PlanningArtifact]:
        if session_id not in self._sessions:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        return [artifact.model_copy(deep=True) for artifact in self._artifacts.get(session_id, [])]

    async def sweep_expired_sessions(
        self,
        *,
        now: datetime,
        intake_ttl_hours: float = 24.0,
        running_ttl_hours: float = 1.0,
    ) -> int:
        changed = 0
        for session in self._sessions.values():
            if session.status == PLANNING_STATUS_INTAKE_PENDING:
                age = now - _utc_aware(session.created_at)
                if age > timedelta(hours=intake_ttl_hours):
                    session.status = PLANNING_STATUS_EXPIRED
                    session.updated_at = now
                    self._events.setdefault(session.id, []).append(
                        _build_event(
                            session.id,
                            "session_expired",
                            {"status": PLANNING_STATUS_EXPIRED},
                        )
                    )
                    changed += 1
            elif session.status.startswith("running"):
                age = now - _utc_aware(session.updated_at)
                if age > timedelta(hours=running_ttl_hours):
                    session.status = PLANNING_STATUS_TIMED_OUT
                    session.updated_at = now
                    self._events.setdefault(session.id, []).append(
                        _build_event(
                            session.id,
                            "session_timed_out",
                            {"status": PLANNING_STATUS_TIMED_OUT},
                        )
                    )
                    changed += 1
        return changed


class FirestorePlanningStore(PlanningStore):
    """Firestore-backed planning persistence."""

    def __init__(self, *, db: Any) -> None:
        self._db = db

    @classmethod
    def from_settings(cls, settings: Settings) -> "FirestorePlanningStore":
        project_id = (settings.gcp_project_id or "").strip()
        if not project_id:
            raise PlanningStoreError(
                "❌ ERROR: GCP_PROJECT_ID is required for firestore planning store"
            )
        if firestore is None:
            raise PlanningStoreError(
                "❌ ERROR: google-cloud-firestore is required for firestore planning store"
            )
        client = firestore.Client(project=project_id)
        return cls(db=client)

    def _session_ref(self, session_id: str) -> Any:
        return self._db.collection("planning_sessions").document(session_id)

    def _messages_ref(self, session_id: str) -> Any:
        return self._session_ref(session_id).collection("messages")

    def _events_ref(self, session_id: str) -> Any:
        return self._session_ref(session_id).collection("events")

    def _artifacts_ref(self, session_id: str) -> Any:
        return self._session_ref(session_id).collection("artifacts")

    async def create_session(self, session: PlanningSession) -> None:
        ref = self._session_ref(session.id)
        if ref.get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session already exists (session_id={session.id})"
            )
        ref.set(_to_document_payload(session))

    async def get_session(self, session_id: str) -> PlanningSession | None:
        snapshot = self._session_ref(session_id).get()
        if not snapshot.exists:
            return None
        return _session_from_payload(session_id, snapshot.to_dict() or {})

    async def update_session(self, session: PlanningSession) -> None:
        ref = self._session_ref(session.id)
        if not ref.get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session.id})"
            )
        ref.set(_to_document_payload(session))

    async def add_message(self, session_id: str, message: PlanningMessage) -> None:
        if not self._session_ref(session_id).get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        self._messages_ref(session_id).document(message.id).set(_to_document_payload(message))

    async def append_event(self, session_id: str, event: PlanningEvent) -> None:
        if not self._session_ref(session_id).get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        self._events_ref(session_id).document(event.id).set(_to_document_payload(event))

    async def get_events(
        self,
        session_id: str,
        after: str | None = None,
    ) -> tuple[list[PlanningEvent], str | None]:
        if not self._session_ref(session_id).get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )

        events = [
            _event_from_payload(doc.to_dict() or {}, doc.id)
            for doc in self._events_ref(session_id).order_by("created_at").stream()
        ]

        if after is None:
            return events, events[-1].id if events else None

        for index, event in enumerate(events):
            if event.id == after:
                events = events[index + 1 :]
                return events, events[-1].id if events else None
        raise PlanningStoreError("❌ ERROR: cursor not found")

    async def get_artifacts(self, session_id: str) -> list[PlanningArtifact]:
        if not self._session_ref(session_id).get().exists:
            raise PlanningStoreError(
                f"❌ ERROR: planning session not found (session_id={session_id})"
            )
        return [
            _artifact_from_payload(doc.to_dict() or {}, doc.id)
            for doc in self._artifacts_ref(session_id).order_by("created_at").stream()
        ]

    async def sweep_expired_sessions(
        self,
        *,
        now: datetime,
        intake_ttl_hours: float = 24.0,
        running_ttl_hours: float = 1.0,
    ) -> int:
        changed = 0
        for doc in self._db.collection("planning_sessions").stream():
            payload = doc.to_dict() or {}
            session = _session_from_payload(doc.id, payload)
            new_status = None
            if session.status == PLANNING_STATUS_INTAKE_PENDING:
                age = now - _utc_aware(session.created_at)
                if age > timedelta(hours=intake_ttl_hours):
                    new_status = PLANNING_STATUS_EXPIRED
            elif session.status.startswith("running"):
                age = now - _utc_aware(session.updated_at)
                if age > timedelta(hours=running_ttl_hours):
                    new_status = PLANNING_STATUS_TIMED_OUT

            if new_status is None:
                continue

            session.status = new_status
            session.updated_at = now
            self._session_ref(session.id).set(_to_document_payload(session))
            event = _build_event(
                session.id,
                "session_expired"
                if new_status == PLANNING_STATUS_EXPIRED
                else "session_timed_out",
                {"status": new_status},
            )
            self._events_ref(session.id).document(event.id).set(_to_document_payload(event))
            changed += 1
        return changed


def build_planning_store(settings: Settings) -> PlanningStore:
    backend = (
        (getattr(settings, "planning_store_backend", "firestore") or "firestore").strip().lower()
    )
    if backend in {"auto", "firestore"}:
        return FirestorePlanningStore.from_settings(settings)
    if backend in {"memory", "in_memory"}:
        return InMemoryPlanningStore()
    raise PlanningStoreError(f"❌ ERROR: unknown planning_store_backend '{backend}'")


def _to_document_payload(
    model: (
        PlanningSession | PlanningMessage | PlanningEvent | PlanningArtifact
    ),
) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _session_from_payload(session_id: str, payload: dict[str, Any]) -> PlanningSession:
    if "id" not in payload:
        payload = {"id": session_id, **payload}
    return PlanningSession.model_validate(payload)


def _event_from_payload(payload: dict[str, Any], doc_id: str) -> PlanningEvent:
    if "id" not in payload:
        payload = {"id": doc_id, **payload}
    return PlanningEvent.model_validate(payload)


def _artifact_from_payload(payload: dict[str, Any], doc_id: str) -> PlanningArtifact:
    if "id" not in payload:
        payload = {"id": doc_id, **payload}
    return PlanningArtifact.model_validate(payload)


def _build_event(session_id: str, event_type: str, payload: dict[str, Any]) -> PlanningEvent:
    return PlanningEvent(
        session_id=session_id,
        event_type=event_type,
        payload=payload,
    )


def _utc_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)
