import base64
import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_cli_agent_spawn_failure_sends_slack(monkeypatch, tmp_path):
    from ace.agents.cli_agent import CliAgent

    # Ensure the agent gets past "ACE_TASK.md missing".
    (tmp_path / "ACE_TASK.md").write_text("do thing", encoding="utf-8")

    posted = []

    class StubNotifier:
        async def safe_post(self, message):
            posted.append(message.text)

    class StubSlackNotifier:
        @classmethod
        def from_settings(cls, settings):
            return StubNotifier()

    # Patch notifier used by CliAgent.
    monkeypatch.setattr("ace.agents.cli_agent.SlackNotifier", StubSlackNotifier)

    # Force an exception early so we hit the spawn-failed path without tmux.
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(CliAgent, "_build_command", _boom)

    agent = CliAgent(backend="claude", model="claude-haiku-4-5")
    result = await agent.run(task="x", context={}, workspace_path=str(tmp_path))
    assert result.status.value == "failed"
    assert posted
    assert "ACE CLI agent spawn failed" in posted[0]
    # Error text can vary depending on which preflight step fails first.


def test_webhook_listener_enqueues_pubsub(monkeypatch):
    from ace.webhooks import app as webhook_app

    published = []

    class StubQueue:
        async def publish(
            self,
            *,
            event,
            payload,
            delivery,
            workflow_id=None,
            issue_key=None,
            project=None,
            target_gcp_project=None,
            action=None,
            queued_at=None,
        ):
            published.append(
                {
                    "event": event,
                    "payload": payload,
                    "delivery": delivery,
                    "workflow_id": workflow_id,
                    "issue_key": issue_key,
                    "project": project,
                    "target_gcp_project": target_gcp_project,
                    "action": action,
                    "queued_at": queued_at,
                }
            )
            return "msg-123"

    # Patch globals used by the FastAPI endpoint.
    old_queue = webhook_app._queue
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app._queue = StubQueue()
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}
    webhook_app._settings = type(
        "SettingsStub",
        (),
        {
            "webhook_service_role": "listener",
            "github_project_name": "Acme Platform",
            "repo_gcp_mapping_path": "docs/repo-gcp-mapping.json",
            "debug": False,
            "slack_bot_token": "",
            "slack_channel_id": "",
        },
    )()

    secret = "test-secret"
    os.environ["GITHUB_WEBHOOK_SECRET"] = secret
    body = json.dumps(
        {
            "action": "created",
            "issue": {"number": 9},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        }
    ).encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    sig = f"sha256={mac.hexdigest()}"

    try:
        client = TestClient(webhook_app.app)
        resp = client.post(
            "/github/webhooks",
            data=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "issue_comment",
                "X-GitHub-Delivery": "delivery-1",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"
        assert resp.json()["message_id"] == "msg-123"
        assert published
        assert published[0]["event"] == "issue_comment"
        assert published[0]["delivery"] == "delivery-1"
        assert published[0]["workflow_id"] == "delivery-1"
        assert published[0]["issue_key"] == "Acme-Corp/widget-api#9"
        assert published[0]["project"] == "Acme Platform"
        assert published[0]["target_gcp_project"] == "widget-prod-123456"
        assert published[0]["action"] == "created"
        assert isinstance(published[0]["queued_at"], str)
        assert published[0]["payload"]["issue"]["number"] == 9
    finally:
        webhook_app._queue = old_queue
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping


def test_webhook_worker_success_sends_slack(monkeypatch):
    from ace.webhooks import app as webhook_app

    posted = []

    class StubNotifier:
        async def safe_post(self, message):
            posted.append(message.text)

    class StubHandler:
        async def handle(self, event, payload, delivery):
            return {
                "status": "triggered",
                "action": "pr_comment",
                "issue": 123,
                "repo": "o/r",
            }

    old_handler = webhook_app._handler
    old_notifier = webhook_app._notifier
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app._handler = StubHandler()
    webhook_app._notifier = StubNotifier()
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}
    webhook_app._settings = type(
        "SettingsStub",
        (),
        {
            "webhook_service_role": "worker",
            "repo_gcp_mapping_path": "docs/repo-gcp-mapping.json",
            "debug": False,
            "slack_bot_token": "",
            "slack_channel_id": "",
        },
    )()

    envelope = {
        "message": {
            "messageId": "1234",
            "data": base64.b64encode(
                json.dumps(
                    {
                        "event": "issue_comment",
                        "delivery": "delivery-1",
                        "queued_at": "2026-02-12T18:00:00+00:00",
                        "correlation": {
                            "delivery_id": "delivery-1",
                            "workflow_id": "delivery-1",
                            "issue_key": "Acme-Corp/widget-api#123",
                            "project": "Acme Platform",
                            "target_gcp_project": "widget-prod-123456",
                            "action": "created",
                        },
                        "payload": {},
                    }
                ).encode("utf-8")
            ).decode("utf-8"),
            "attributes": {
                "delivery_id": "delivery-1",
                "workflow_id": "delivery-1",
                "issue_key": "Acme-Corp/widget-api#123",
                "project": "Acme Platform",
                "target_gcp_project": "widget-prod-123456",
                "action": "created",
            },
        }
    }

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/worker", json=envelope)
        assert resp.status_code == 200
        assert posted
        assert "Webhook processed" in posted[0]
        assert "event=issue_comment" in posted[0]
        assert resp.json()["workflow_id"] == "delivery-1"
        assert resp.json()["pubsub_message_id"] == "1234"
        assert resp.json()["queued_at"] == "2026-02-12T18:00:00+00:00"
        assert resp.json()["queue_delay_ms"] is not None
        assert resp.json()["resolution"] == "success"
    finally:
        webhook_app._handler = old_handler
        webhook_app._notifier = old_notifier
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping


def test_webhook_listener_lifecycle_logs(monkeypatch):
    from ace.webhooks import app as webhook_app

    events = []

    class StubLogger:
        def __init__(self):
            self._bound = {}

        def bind(self, **kwargs):
            self._bound.update(kwargs)
            return self

        def info(self, event_name, **fields):
            merged = dict(self._bound)
            merged.update(fields)
            events.append({"event_name": event_name, "fields": merged})

    class StubQueue:
        async def publish(
            self,
            *,
            event,
            payload,
            delivery,
            workflow_id=None,
            issue_key=None,
            project=None,
            target_gcp_project=None,
            action=None,
            queued_at=None,
        ):
            return "msg-xyz"

    old_logger = webhook_app.logger
    old_queue = webhook_app._queue
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app.logger = StubLogger()
    webhook_app._queue = StubQueue()
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}
    webhook_app._settings = type(
        "SettingsStub",
        (),
        {
            "webhook_service_role": "listener",
            "github_project_name": "Acme Platform",
            "repo_gcp_mapping_path": "docs/repo-gcp-mapping.json",
            "webhook_pubsub_topic": "projects/p/topics/t",
            "debug": False,
            "slack_bot_token": "",
            "slack_channel_id": "",
        },
    )()

    secret = "test-secret"
    os.environ["GITHUB_WEBHOOK_SECRET"] = secret
    payload = {
        "action": "created",
        "issue": {"number": 34},
        "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
    }
    body = json.dumps(payload).encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    sig = f"sha256={mac.hexdigest()}"

    try:
        client = TestClient(webhook_app.app)
        resp = client.post(
            "/github/webhooks",
            data=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "issue_comment",
                "X-GitHub-Delivery": "delivery-42",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 202
        lifecycle = [e for e in events if e["event_name"] == "webhook_lifecycle"]
        assert [e["fields"]["stage"] for e in lifecycle] == ["webhook_received", "webhook_enqueued"]
        for entry in lifecycle:
            fields = entry["fields"]
            assert fields["event"] == "issue_comment"
            assert fields["delivery_id"] == "delivery-42"
            assert fields["workflow_id"] == "delivery-42"
            assert fields["issue_key"] == "Acme-Corp/widget-api#34"
            assert fields["project"] == "Acme Platform"
            assert fields["target_gcp_project"] == "widget-prod-123456"
        assert lifecycle[1]["fields"]["pubsub_message_id"] == "msg-xyz"
        assert isinstance(lifecycle[1]["fields"]["queued_at"], str)
    finally:
        webhook_app.logger = old_logger
        webhook_app._queue = old_queue
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping


def test_webhook_worker_lifecycle_logs_blocked(monkeypatch):
    from ace.webhooks import app as webhook_app

    events = []

    class StubLogger:
        def __init__(self):
            self._bound = {}

        def bind(self, **kwargs):
            self._bound.update(kwargs)
            return self

        def info(self, event_name, **fields):
            merged = dict(self._bound)
            merged.update(fields)
            events.append({"event_name": event_name, "fields": merged})

    class StubHandler:
        async def handle(self, event, payload, delivery):
            return {"status": "blocked", "action": "Ready"}

    old_logger = webhook_app.logger
    old_handler = webhook_app._handler
    old_notifier = webhook_app._notifier
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app.logger = StubLogger()
    webhook_app._handler = StubHandler()
    webhook_app._notifier = None
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}
    webhook_app._settings = type(
        "SettingsStub",
        (),
        {
            "webhook_service_role": "worker",
            "github_project_name": "Acme Platform",
            "repo_gcp_mapping_path": "docs/repo-gcp-mapping.json",
            "debug": False,
            "slack_bot_token": "",
            "slack_channel_id": "",
        },
    )()

    envelope = {
        "message": {
            "messageId": "1234",
            "data": base64.b64encode(
                json.dumps(
                    {
                        "event": "projects_v2_item",
                        "delivery": "delivery-11",
                        "workflow_id": "wf-11",
                        "queued_at": "2026-02-12T18:00:00+00:00",
                        "correlation": {
                            "delivery_id": "delivery-11",
                            "workflow_id": "wf-11",
                            "issue_key": "Acme-Corp/widget-api#34",
                            "project": "Acme Platform",
                            "target_gcp_project": "widget-prod-123456",
                            "action": "edited",
                        },
                        "payload": {
                            "action": "edited",
                            "issue": {"number": 34},
                            "repository": {
                                "name": "widget-api",
                                "owner": {"login": "Acme-Corp"},
                            },
                        },
                    }
                ).encode("utf-8")
            ).decode("utf-8"),
        }
    }

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/worker", json=envelope)
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "blocked"
        assert resp.json()["pubsub_message_id"] == "1234"
        assert resp.json()["queue_delay_ms"] is not None

        lifecycle = [e for e in events if e["event_name"] == "webhook_lifecycle"]
        assert [e["fields"]["stage"] for e in lifecycle] == [
            "worker_dequeued",
            "worker_started",
            "agent_started",
            "agent_finished",
            "final_resolution",
        ]
        for entry in lifecycle:
            fields = entry["fields"]
            assert fields["workflow_id"] == "wf-11"
            assert fields["delivery_id"] == "delivery-11"
            assert fields["issue_key"] == "Acme-Corp/widget-api#34"
            assert fields["target_gcp_project"] == "widget-prod-123456"
        assert lifecycle[0]["fields"]["pubsub_message_id"] == "1234"
        assert lifecycle[0]["fields"]["queue_delay_ms"] is not None

        assert lifecycle[-1]["fields"]["resolution"] == "blocked"
    finally:
        webhook_app.logger = old_logger
        webhook_app._handler = old_handler
        webhook_app._notifier = old_notifier
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping


def test_webhook_worker_failure_logs_resolution(monkeypatch):
    from ace.webhooks import app as webhook_app

    events = []

    class StubLogger:
        def __init__(self):
            self._bound = {}

        def bind(self, **kwargs):
            self._bound.update(kwargs)
            return self

        def info(self, event_name, **fields):
            merged = dict(self._bound)
            merged.update(fields)
            events.append({"event_name": event_name, "fields": merged})

    class StubHandler:
        async def handle(self, event, payload, delivery):
            raise RuntimeError("spawn_failed")

    old_logger = webhook_app.logger
    old_handler = webhook_app._handler
    old_notifier = webhook_app._notifier
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app.logger = StubLogger()
    webhook_app._handler = StubHandler()
    webhook_app._notifier = None
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}
    webhook_app._settings = type(
        "SettingsStub",
        (),
        {
            "webhook_service_role": "worker",
            "github_project_name": "Acme Platform",
            "repo_gcp_mapping_path": "docs/repo-gcp-mapping.json",
            "debug": False,
            "slack_bot_token": "",
            "slack_channel_id": "",
        },
    )()

    envelope = {
        "message": {
            "messageId": "1234",
            "data": base64.b64encode(
                json.dumps(
                    {
                        "event": "issue_comment",
                        "delivery": "delivery-1",
                        "queued_at": "2026-02-12T18:00:00+00:00",
                        "correlation": {
                            "delivery_id": "delivery-1",
                            "workflow_id": "wf-1",
                            "issue_key": "Acme-Corp/widget-api#34",
                            "project": "Acme Platform",
                            "target_gcp_project": "widget-prod-123456",
                        },
                        "payload": {"issue": {"number": 34}},
                    }
                ).encode("utf-8")
            ).decode("utf-8"),
        }
    }

    try:
        client = TestClient(webhook_app.app, raise_server_exceptions=False)
        resp = client.post("/internal/pubsub/worker", json=envelope)
        assert resp.status_code == 500
        lifecycle = [e for e in events if e["event_name"] == "webhook_lifecycle"]
        assert lifecycle[-1]["fields"]["stage"] == "final_resolution"
        assert lifecycle[-1]["fields"]["resolution"] == "failure"
    finally:
        webhook_app.logger = old_logger
        webhook_app._handler = old_handler
        webhook_app._notifier = old_notifier
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping
