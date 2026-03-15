"""Pub/Sub queue utilities for collaborative PR review jobs."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

from google.cloud import pubsub_v1

from ace.config.settings import Settings


@dataclass(frozen=True)
class PRReviewJob:
    """Normalized PR review job payload published by webhook workers."""

    repo_owner: str
    repo_name: str
    pr_number: int
    head_sha: str
    base_ref: str
    action: str
    workflow_id: str
    idempotency_key: str
    installation_id: int
    delivery_id: str | None = None
    project: str | None = None
    target_gcp_project: str | None = None
    queued_at: str | None = None
    message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
            "action": self.action,
            "workflow_id": self.workflow_id,
            "idempotency_key": self.idempotency_key,
            "installation_id": self.installation_id,
            "delivery_id": self.delivery_id,
            "project": self.project,
            "target_gcp_project": self.target_gcp_project,
            "queued_at": self.queued_at,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PRReviewJob":
        return cls(
            repo_owner=_required_string(payload.get("repo_owner"), "repo_owner"),
            repo_name=_required_string(payload.get("repo_name"), "repo_name"),
            pr_number=_required_int(payload.get("pr_number"), "pr_number"),
            head_sha=_required_string(payload.get("head_sha"), "head_sha"),
            base_ref=_required_string(payload.get("base_ref"), "base_ref"),
            action=_required_string(payload.get("action"), "action"),
            workflow_id=_required_string(payload.get("workflow_id"), "workflow_id"),
            idempotency_key=_required_string(payload.get("idempotency_key"), "idempotency_key"),
            installation_id=_required_int(payload.get("installation_id"), "installation_id"),
            delivery_id=_optional_string(payload.get("delivery_id"), "delivery_id"),
            project=_optional_string(payload.get("project"), "project"),
            target_gcp_project=_optional_string(
                payload.get("target_gcp_project"),
                "target_gcp_project",
            ),
            queued_at=_optional_string(payload.get("queued_at"), "queued_at"),
            message_id=_optional_string(payload.get("message_id"), "message_id"),
        )


class PRReviewPubSubQueue:
    """Publish normalized PR review jobs to Pub/Sub."""

    def __init__(self, *, publisher: pubsub_v1.PublisherClient, topic_path: str) -> None:
        self._publisher = publisher
        self._topic_path = topic_path

    @classmethod
    def from_settings(cls, settings: Settings) -> "PRReviewPubSubQueue":
        topic_path = (settings.pr_review_pubsub_topic or "").strip()
        if not topic_path:
            raise ValueError("❌ ERROR: PR_REVIEW_PUBSUB_TOPIC is required for PR review queue.")
        if not topic_path.startswith("projects/"):
            raise ValueError(
                "❌ ERROR: PR_REVIEW_PUBSUB_TOPIC must be a full topic path "
                "(projects/<project>/topics/<topic>)."
            )
        publisher = pubsub_v1.PublisherClient()
        return cls(publisher=publisher, topic_path=topic_path)

    async def publish(self, job: PRReviewJob) -> str:
        envelope = {"schema_version": "1", "job": job.to_dict()}
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        attrs = {
            "repo_owner": job.repo_owner,
            "repo_name": job.repo_name,
            "pr_number": str(job.pr_number),
            "head_sha": job.head_sha,
            "workflow_id": job.workflow_id,
            "idempotency_key": job.idempotency_key,
            "installation_id": str(job.installation_id),
            "action": job.action,
        }
        if job.delivery_id:
            attrs["delivery_id"] = job.delivery_id
        if job.project:
            attrs["project"] = job.project
        if job.target_gcp_project:
            attrs["target_gcp_project"] = job.target_gcp_project
        if job.queued_at:
            attrs["queued_at"] = job.queued_at
        publish_future = self._publisher.publish(self._topic_path, body, **attrs)
        return await asyncio.to_thread(lambda: str(publish_future.result(timeout=30)))


def decode_pubsub_push(body: dict[str, Any]) -> PRReviewJob:
    """Decode a Pub/Sub push payload into a PR review job."""
    message = body.get("message")
    if not isinstance(message, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing message object")

    raw_data = message.get("data")
    if not isinstance(raw_data, str) or not raw_data:
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing base64 message.data")

    try:
        decoded = base64.b64decode(raw_data, validate=True).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive decode guard
        raise ValueError(
            f"❌ ERROR: invalid_pubsub_push: invalid base64 message.data ({exc})"
        ) from exc

    try:
        envelope = json.loads(decoded)
    except Exception as exc:
        raise ValueError(f"❌ ERROR: invalid_pubsub_push: invalid JSON payload ({exc})") from exc

    job_payload = envelope.get("job")
    if not isinstance(job_payload, dict):
        raise ValueError("❌ ERROR: invalid_pubsub_push: missing job")

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

    message_id = message.get("messageId")
    if message_id is not None:
        job_payload = {**job_payload, "message_id": message_id}
    return PRReviewJob.from_dict(job_payload)


def _required_string(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"❌ ERROR: {field_name} must be a non-empty string")


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"❌ ERROR: {field_name} must be a non-empty string when provided")


def _required_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"❌ ERROR: {field_name} must be an integer") from exc
