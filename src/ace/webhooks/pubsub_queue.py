"""Pub/Sub queue utilities for webhook listener/worker split."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

from google.cloud import pubsub_v1

from ace.config.settings import Settings


@dataclass(frozen=True)
class QueuedWebhookEvent:
    event: str
    payload: dict[str, Any]
    delivery: str | None
    workflow_id: str | None = None
    issue_key: str | None = None
    project: str | None = None
    action: str | None = None
    queued_at: str | None = None
    message_id: str | None = None


class PubSubWebhookQueue:
    """Publish GitHub webhook events into Pub/Sub."""

    def __init__(self, *, publisher: pubsub_v1.PublisherClient, topic_path: str) -> None:
        self._publisher = publisher
        self._topic_path = topic_path

    @classmethod
    def from_settings(cls, settings: Settings) -> PubSubWebhookQueue:
        topic_path = (settings.webhook_pubsub_topic or "").strip()
        if not topic_path:
            raise ValueError("❌ ERROR: WEBHOOK_PUBSUB_TOPIC is required for listener mode.")
        if not topic_path.startswith("projects/"):
            raise ValueError(
                "❌ ERROR: WEBHOOK_PUBSUB_TOPIC must be a full topic path "
                "(projects/<project>/topics/<topic>)."
            )
        publisher = pubsub_v1.PublisherClient()
        return cls(publisher=publisher, topic_path=topic_path)

    async def publish(
        self,
        *,
        event: str,
        payload: dict[str, Any],
        delivery: str | None,
        workflow_id: str | None = None,
        issue_key: str | None = None,
        project: str | None = None,
        action: str | None = None,
        queued_at: str | None = None,
    ) -> str:
        correlation = {
            "delivery_id": delivery,
            "workflow_id": workflow_id,
            "issue_key": issue_key,
            "project": project,
            "action": action,
        }
        envelope = {
            "schema_version": "2",
            "event": event,
            "payload": payload,
            "delivery": delivery,
            "workflow_id": workflow_id,
            "queued_at": queued_at,
            "correlation": correlation,
        }
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        attrs = {"event": event}
        if delivery:
            attrs["delivery"] = delivery
            attrs["delivery_id"] = delivery
        if workflow_id:
            attrs["workflow_id"] = workflow_id
        if issue_key:
            attrs["issue_key"] = issue_key
        if project:
            attrs["project"] = project
        if action:
            attrs["action"] = action
        if queued_at:
            attrs["queued_at"] = queued_at
        publish_future = self._publisher.publish(self._topic_path, body, **attrs)
        return await asyncio.to_thread(lambda: str(publish_future.result(timeout=30)))


def decode_pubsub_push(body: dict[str, Any]) -> QueuedWebhookEvent:
    """Decode a Pub/Sub push request body into a queue event."""
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
    payload = envelope.get("payload")
    attrs = message.get("attributes")
    if attrs is None:
        attrs = {}
    if not isinstance(attrs, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: message.attributes must be an object")
    for key, value in attrs.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                "❌ ERROR: invalid_pubsub_push: message.attributes must be string keys and values"
            )

    correlation = envelope.get("correlation")
    if correlation is None:
        correlation = {}
    if not isinstance(correlation, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: correlation must be an object")

    delivery = _first_string(
        envelope.get("delivery"),
        correlation.get("delivery_id"),
        attrs.get("delivery_id"),
        attrs.get("delivery"),
    )
    workflow_id = _first_string(
        envelope.get("workflow_id"),
        correlation.get("workflow_id"),
        attrs.get("workflow_id"),
    )
    issue_key = _first_string(
        correlation.get("issue_key"),
        attrs.get("issue_key"),
    )
    project = _first_string(
        correlation.get("project"),
        attrs.get("project"),
    )
    action = _first_string(
        correlation.get("action"),
        payload.get("action") if isinstance(payload, dict) else None,
        attrs.get("action"),
    )
    queued_at = _first_string(
        envelope.get("queued_at"),
        attrs.get("queued_at"),
    )
    message_id = message.get("messageId")

    if not isinstance(event, str) or not event:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing event")
    if not isinstance(payload, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: payload must be an object")
    if message_id is not None and not isinstance(message_id, str):
        raise ValueError("❌ ERROR: invalid_pubsub_push: messageId must be string")

    return QueuedWebhookEvent(
        event=event,
        payload=payload,
        delivery=delivery,
        workflow_id=workflow_id,
        issue_key=issue_key,
        project=project,
        action=action,
        queued_at=queued_at,
        message_id=message_id,
    )


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            continue
        raise ValueError(
            "❌ ERROR: invalid_pubsub_push: expected string value in correlation fields"
        )
    return None
