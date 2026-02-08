import hashlib
import hmac
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


def test_webhook_success_sends_slack(monkeypatch):
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

    # Patch globals used by the FastAPI endpoint.
    old_handler = webhook_app._handler
    old_notifier = webhook_app._notifier
    webhook_app._handler = StubHandler()
    webhook_app._notifier = StubNotifier()

    secret = "test-secret"
    os.environ["GITHUB_WEBHOOK_SECRET"] = secret
    body = b"{}"
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
        assert resp.status_code == 200
        assert posted
        assert "Webhook processed" in posted[0]
        assert "event=issue_comment" in posted[0]
    finally:
        webhook_app._handler = old_handler
        webhook_app._notifier = old_notifier
