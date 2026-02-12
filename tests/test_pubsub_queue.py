import base64
import json

import pytest

from ace.webhooks.pubsub_queue import PubSubWebhookQueue, decode_pubsub_push


class _StubPublishFuture:
    def __init__(self, message_id: str):
        self._message_id = message_id

    def result(self, timeout: int | None = None) -> str:
        return self._message_id


class _StubPublisher:
    def __init__(self):
        self.calls = []

    def publish(self, topic_path: str, body: bytes, **attrs):
        self.calls.append({"topic_path": topic_path, "body": body, "attrs": attrs})
        return _StubPublishFuture("pubsub-123")


@pytest.mark.asyncio
async def test_publish_includes_correlation_payload_and_attributes():
    publisher = _StubPublisher()
    queue = PubSubWebhookQueue(
        publisher=publisher,
        topic_path="projects/test/topics/appforge-webhooks",
    )

    message_id = await queue.publish(
        event="issue_comment",
        payload={"action": "created"},
        delivery="delivery-1",
        workflow_id="wf-1",
        issue_key="Day-in-the-Country-LLC/digido#34",
        project="Appforge",
        action="created",
        queued_at="2026-02-12T18:00:00+00:00",
    )

    assert message_id == "pubsub-123"
    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    assert call["attrs"]["delivery_id"] == "delivery-1"
    assert call["attrs"]["workflow_id"] == "wf-1"
    assert call["attrs"]["issue_key"] == "Day-in-the-Country-LLC/digido#34"
    assert call["attrs"]["project"] == "Appforge"
    assert call["attrs"]["queued_at"] == "2026-02-12T18:00:00+00:00"

    envelope = json.loads(call["body"].decode("utf-8"))
    assert envelope["schema_version"] == "2"
    assert envelope["queued_at"] == "2026-02-12T18:00:00+00:00"
    assert envelope["correlation"]["delivery_id"] == "delivery-1"
    assert envelope["correlation"]["workflow_id"] == "wf-1"
    assert envelope["correlation"]["issue_key"] == "Day-in-the-Country-LLC/digido#34"
    assert envelope["correlation"]["project"] == "Appforge"
    assert envelope["correlation"]["action"] == "created"


def test_decode_pubsub_push_reads_correlation_fields():
    envelope = {
        "event": "projects_v2_item",
        "payload": {"action": "edited"},
        "delivery": "delivery-2",
        "workflow_id": "wf-2",
        "queued_at": "2026-02-12T18:00:00+00:00",
        "correlation": {
            "issue_key": "Day-in-the-Country-LLC/digido#99",
            "project": "Appforge",
            "action": "edited",
        },
    }
    body = {
        "message": {
            "messageId": "1234",
            "data": base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("utf-8"),
            "attributes": {
                "delivery_id": "delivery-2",
                "workflow_id": "wf-2",
                "issue_key": "Day-in-the-Country-LLC/digido#99",
                "project": "Appforge",
                "queued_at": "2026-02-12T18:00:00+00:00",
            },
        }
    }

    queued = decode_pubsub_push(body)
    assert queued.message_id == "1234"
    assert queued.delivery == "delivery-2"
    assert queued.workflow_id == "wf-2"
    assert queued.issue_key == "Day-in-the-Country-LLC/digido#99"
    assert queued.project == "Appforge"
    assert queued.action == "edited"
    assert queued.queued_at == "2026-02-12T18:00:00+00:00"


def test_decode_pubsub_push_accepts_legacy_envelope():
    envelope = {
        "event": "issue_comment",
        "payload": {},
        "delivery": "delivery-legacy",
        "workflow_id": "wf-legacy",
    }
    body = {
        "message": {
            "messageId": "legacy-1",
            "data": base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("utf-8"),
        }
    }

    queued = decode_pubsub_push(body)
    assert queued.message_id == "legacy-1"
    assert queued.delivery == "delivery-legacy"
    assert queued.workflow_id == "wf-legacy"
    assert queued.issue_key is None
    assert queued.project is None
    assert queued.queued_at is None
