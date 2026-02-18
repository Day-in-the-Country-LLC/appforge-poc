"""Pub/Sub queue utilities for planner jobs."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

from google.cloud import pubsub_v1


@dataclass(frozen=True)
class PlanningJob:
    """Payload contract published to the planner job queue."""

    session_id: str
    project_slug: str
    created_at: str
    message_id: str | None = None


@dataclass(frozen=True)
class QueuedPlanningJob:
    """Decoded Pub/Sub push payload for planner jobs."""

    event: str
    payload: PlanningJob
    message_id: str | None = None


class PubSubPlannerQueue:
    """Publish planning jobs into Pub/Sub."""

    def __init__(self, *, publisher: pubsub_v1.PublisherClient, topic_path: str) -> None:
        self._publisher = publisher
        self._topic_path = topic_path

    @classmethod
    def from_settings(cls, settings: Any) -> "PubSubPlannerQueue":
        topic_path = (settings.planning_pubsub_topic or "").strip()
        if not topic_path:
            raise ValueError("❌ ERROR: PLANNING_PUBSUB_TOPIC is required for planner jobs.")
        if not topic_path.startswith("projects/"):
            raise ValueError(
                "❌ ERROR: PLANNING_PUBSUB_TOPIC must be a full topic path "
                "(projects/<project>/topics/<topic>)."
            )
        publisher = pubsub_v1.PublisherClient()
        return cls(publisher=publisher, topic_path=topic_path)

    async def publish(
        self,
        *,
        session_id: str,
        project_slug: str,
        created_at: str,
    ) -> str:
        envelope = {
            "schema_version": "1",
            "event": "planner.start",
            "payload": {
                "session_id": session_id,
                "project_slug": project_slug,
                "created_at": created_at,
            },
            "queued_at": created_at,
            "correlation": {
                "session_id": session_id,
                "project_slug": project_slug,
            },
        }
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        attrs = {
            "event": "planner.start",
            "session_id": session_id,
            "project_slug": project_slug,
        }
        publish_future = self._publisher.publish(self._topic_path, body, **attrs)
        return await asyncio.to_thread(lambda: str(publish_future.result(timeout=30)))


def decode_pubsub_push(body: dict[str, Any]) -> QueuedPlanningJob:
    """Decode a Pub/Sub push body into a planning job."""
    message = body.get("message")
    if not isinstance(message, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing message object")

    raw_data = message.get("data")
    if not isinstance(raw_data, str) or not raw_data:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing base64 message.data")

    try:
        decoded = base64.b64decode(raw_data, validate=True).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive decode guard
        raise ValueError(f"❌ ERROR: invalid_pubsub_push: invalid base64 data ({exc})") from exc

    try:
        envelope = json.loads(decoded)
    except Exception as exc:
        raise ValueError(f"❌ ERROR: invalid_pubsub_push: invalid JSON payload ({exc})") from exc

    event = envelope.get("event")
    attrs = message.get("attributes")
    if attrs is None:
        attrs = {}
    if not isinstance(attrs, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: message.attributes must be an object")

    correlation = envelope.get("correlation")
    if correlation is None:
        correlation = {}
    if not isinstance(correlation, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: correlation must be an object")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: payload must be an object")

    session_id = _first_string(
        payload.get("session_id"),
        correlation.get("session_id"),
        attrs.get("session_id"),
    )
    project_slug = _first_string(
        payload.get("project_slug"),
        correlation.get("project_slug"),
        attrs.get("project_slug"),
    )
    created_at = _first_string(
        payload.get("created_at"),
        envelope.get("queued_at"),
    )

    if not isinstance(event, str) or not event:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing event")
    if session_id is None:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing session_id")
    if project_slug is None:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing project_slug")
    if created_at is None:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing created_at")
    message_id = message.get("messageId")
    if message_id is not None and not isinstance(message_id, str):
        raise ValueError("❌ ERROR: invalid_pubsub_push: messageId must be string")

    job = PlanningJob(
        session_id=session_id,
        project_slug=project_slug,
        created_at=created_at,
        message_id=message_id,
    )

    return QueuedPlanningJob(event=event, payload=job, message_id=message_id)


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            continue
        raise ValueError(
            "❌ ERROR: invalid_pubsub_push: expected string value in planning job fields"
        )
    return None
