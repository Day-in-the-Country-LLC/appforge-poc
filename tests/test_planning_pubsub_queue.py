"""Tests for planner Pub/Sub queue contract."""

from __future__ import annotations

import base64
import json

import pytest

from ace.config.settings import Settings
from ace.planning.models import PlanningMode
from ace.planning.pubsub_queue import PlanningJob, PubSubPlannerQueue, decode_pubsub_push


class _FakePublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, dict[str, str]]] = []

    def publish(self, topic: str, data: bytes, **attrs: str):
        self.publishes.append((topic, data, attrs))

        class _Future:
            def result(self, timeout: float | None = None) -> str:  # pragma: no cover
                return "msg-123"

        return _Future()


@pytest.mark.asyncio
async def test_planner_queue_publish_uses_expected_schema(monkeypatch) -> None:
    fake_publisher = _FakePublisher()
    monkeypatch.setattr(
        "ace.planning.pubsub_queue.pubsub_v1.PublisherClient",
        lambda *args, **kwargs: fake_publisher,
    )
    queue = PubSubPlannerQueue.from_settings(
        Settings(planning_pubsub_topic="projects/test/topics/appforge-planner-jobs")
    )

    message_id = await queue.publish(
        session_id="sess-1",
        project_slug="example-project",
        mode="plan_only",
        created_at="2026-02-15T00:00:00Z",
    )
    assert message_id == "msg-123"

    assert len(fake_publisher.publishes) == 1
    topic, payload, attrs = fake_publisher.publishes[0]
    assert topic == "projects/test/topics/appforge-planner-jobs"
    envelope = json.loads(payload.decode("utf-8"))
    assert envelope["event"] == "planner.start"
    assert envelope["payload"]["session_id"] == "sess-1"
    assert envelope["payload"]["mode"] == "plan_only"
    assert attrs == {
        "event": "planner.start",
        "session_id": "sess-1",
        "project_slug": "example-project",
        "mode": "plan_only",
    }


def test_decode_planner_pubsub_push() -> None:
    raw = {
        "schema_version": "1",
        "event": "planner.start",
        "payload": {
            "session_id": "sess-2",
            "project_slug": "example-project",
            "mode": "plan_only",
            "created_at": "2026-02-15T00:00:00Z",
        },
        "queued_at": "2026-02-15T00:00:01Z",
    }
    encoded = base64.b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")
    push_body = {
        "message": {
            "data": encoded,
            "messageId": "message-id-456",
            "attributes": {
                "event": "planner.start",
                "session_id": "sess-2",
                "project_slug": "example-project",
                "mode": "plan_only",
            },
        }
    }
    queued = decode_pubsub_push(push_body)
    assert queued.event == "planner.start"
    assert isinstance(queued.payload, PlanningJob)
    assert queued.payload.session_id == "sess-2"
    assert queued.payload.mode == PlanningMode.PLAN_ONLY
    assert queued.message_id == "message-id-456"


def test_planner_queue_requires_topic() -> None:
    with pytest.raises(ValueError, match="PLANNING_PUBSUB_TOPIC is required"):
        PubSubPlannerQueue.from_settings(Settings(planning_pubsub_topic=""))
