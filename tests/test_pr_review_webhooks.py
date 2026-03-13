import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ace.pr_review import InMemoryPRReviewSessionStore, PRReviewStatus
from ace.pr_review.job_store import InMemoryPRReviewJobStore
from ace.pr_review.pubsub_queue import PRReviewJob
from ace.webhooks.handlers import WebhookHandler
from ace.webhooks.event_router import WorkEvent

from ace.pr_review.context import PRContext


class _StubLogger:
    def __init__(self) -> None:
        self.events = []
        self._bound = {}

    def bind(self, **kwargs):
        child = _StubLogger()
        child.events = self.events
        child._bound = {**self._bound, **kwargs}
        return child

    def info(self, event_name, **fields):
        merged = dict(self._bound)
        merged.update(fields)
        self.events.append({"event_name": event_name, "fields": merged})


def _build_pr_review_verdict_payload(*, reviewer_model: str, verdict: str, round_number: int) -> str:
    return json.dumps(
        {
            "reviewer_model": reviewer_model,
            "verdict": verdict,
            "confidence": "high",
            "blocking_findings": [],
            "non_blocking_findings": [],
            "suggested_comments": [],
            "reasoning": "reviewed",
            "round_number": round_number,
        },
        ensure_ascii=True,
    )


def _build_pr_context(*, base_branch: str = "qa") -> PRContext:
    return PRContext(
        pr_number=42,
        title="Improve flow",
        body="Review summary",
        diff="diff",
        files_changed=[],
        commits=[],
        existing_comments=[],
        check_runs=[],
        labels=[],
        author="alice",
        base_branch=base_branch,
        head_branch="feature/review",
    )


class _StubPRReviewQueue:
    def __init__(self) -> None:
        self.jobs: list[PRReviewJob] = []

    async def publish(self, job: PRReviewJob) -> str:
        self.jobs.append(job)
        return f"pr-msg-{len(self.jobs)}"


class _SettingsStub:
    pr_review_enabled = True
    pr_review_allowed_repos = ""
    pr_review_pubsub_topic = "projects/test/topics/pr-review-jobs"
    github_project_name = "Acme Platform"
    github_org = "Acme-Corp"


class _SettingsDisabledStub(_SettingsStub):
    pr_review_enabled = False


class _SettingsRepoAllowlistStub(_SettingsStub):
    pr_review_allowed_repos = "Acme-Corp/widget-api"


class _SettingsMissingTopicStub(_SettingsStub):
    pr_review_pubsub_topic = ""


class _DispatchSettingsStub(_SettingsStub):
    appforge_mcp_url = "http://mcp.example"


class _WorkerSettingsStub:
    webhook_service_role = "worker"
    github_project_name = "Acme Platform"
    repo_gcp_mapping_path = "docs/repo-gcp-mapping.json"
    debug = False
    slack_bot_token = ""
    slack_channel_id = ""
    gcp_project_id = "local-test-gcp"
    secrets_backend = "env"
    openai_api_key = "test-openai-key"
    claude_api_key = "test-claude-key"
    pr_review_codex_model = "gpt-5.2-codex"
    pr_review_claude_model = "claude-opus-4-6"
    pr_review_codex_max_tokens = 1024
    pr_review_claude_max_tokens = 1024
    pr_review_codex_reasoning_effort = "high"
    pr_review_claude_reasoning_effort = "high"
    pr_review_max_rounds = 2
    pr_review_consensus_mode = "both_approve"
    pr_review_target_branch = "qa"
    pr_review_require_checks = False
    pr_review_diff_max_chars = 2000


def _build_pull_request_payload(*, action: str, head_sha: str, draft: bool = False) -> dict:
    return {
        "action": action,
        "number": 42,
        "installation": {"id": 999},
        "repository": {
            "name": "widget-api",
            "owner": {"login": "Acme-Corp"},
        },
        "pull_request": {
            "number": 42,
            "draft": draft,
            "head": {"sha": head_sha},
            "base": {"ref": "qa"},
        },
    }


@pytest.mark.asyncio
async def test_pull_request_handler_enqueues_review_job(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="opened", head_sha="abc123"),
        "delivery-42",
        workflow_id="wf-42",
    )

    assert result["status"] == "enqueued"
    assert result["action"] == "pr_review"
    assert result["pr_number"] == 42
    assert result["head_sha"] == "abc123"
    assert len(queue.jobs) == 1
    assert queue.jobs[0].workflow_id == "wf-42"
    assert queue.jobs[0].idempotency_key == "Acme-Corp/widget-api:42:abc123"

    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert [event["fields"]["stage"] for event in lifecycle] == [
        "pr_review_started",
        "pr_review_enqueued",
    ]
    assert lifecycle[1]["fields"]["pubsub_message_id"] == "pr-msg-1"


@pytest.mark.asyncio
async def test_pull_request_handler_skips_when_pr_review_disabled(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsDisabledStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="opened", head_sha="abc123"),
        "disabled-1",
        workflow_id="wf-disabled",
    )

    assert result == {"status": "ignored", "reason": "pr_review_disabled"}
    assert queue.jobs == []
    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert [event["fields"]["stage"] for event in lifecycle] == ["pr_review_skipped"]


@pytest.mark.asyncio
async def test_pull_request_handler_skips_when_repo_not_allowed(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)
    handler.settings = _SettingsStub()
    handler.settings.pr_review_allowed_repos = "acme-corp/other-repo,other-org/widget-api"

    result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="opened", head_sha="abc123"),
        "delivery-disallowed",
        workflow_id="wf-disallowed",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "repo_not_allowed"
    assert queue.jobs == []
    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert [event["fields"]["stage"] for event in lifecycle] == ["pr_review_skipped"]


@pytest.mark.asyncio
async def test_pull_request_handler_enqueues_when_repo_is_allowlisted(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsRepoAllowlistStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="opened", head_sha="abc123"),
        "delivery-allowed",
        workflow_id="wf-allowed",
    )

    assert result["status"] == "enqueued"
    assert result["action"] == "pr_review"
    assert result["pr_number"] == 42
    assert len(queue.jobs) == 1
    assert queue.jobs[0].idempotency_key == "Acme-Corp/widget-api:42:abc123"


@pytest.mark.asyncio
async def test_pull_request_handler_fails_fast_without_pr_review_topic(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsMissingTopicStub(),
        app_auth=object(),
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    with pytest.raises(ValueError, match="PR_REVIEW_PUBSUB_TOPIC is required"):
        await handler.handle(
            "pull_request",
            _build_pull_request_payload(action="opened", head_sha="abc123"),
            "delivery-1",
            workflow_id="wf-1",
        )

    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert [event["fields"]["stage"] for event in lifecycle] == [
        "pr_review_started",
        "pr_review_error",
    ]


@pytest.mark.asyncio
async def test_pull_request_handler_skips_duplicate_head_sha(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    payload = _build_pull_request_payload(action="opened", head_sha="abc123")

    first = await handler.handle("pull_request", payload, "delivery-1", workflow_id="wf-1")
    second = await handler.handle("pull_request", payload, "delivery-2", workflow_id="wf-2")

    assert first["status"] == "enqueued"
    assert second["status"] == "skipped"
    assert second["reason"] == "review_in_progress"
    assert len(queue.jobs) == 1

    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert [event["fields"]["stage"] for event in lifecycle] == [
        "pr_review_started",
        "pr_review_enqueued",
        "pr_review_started",
        "pr_review_skipped",
    ]


@pytest.mark.asyncio
async def test_pull_request_handler_skips_duplicate_head_sha_under_rapid_repeat_events(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    payload = _build_pull_request_payload(action="opened", head_sha="abc123")
    for idx in range(6):
        result = await handler.handle(
            "pull_request",
            payload,
            f"delivery-rapid-{idx}",
            workflow_id="wf-rapid",
        )
        if idx == 0:
            assert result["status"] == "enqueued"
        else:
            assert result["status"] == "skipped"
            assert result["reason"] == "review_in_progress"
            assert result["pr_number"] == 42
            assert result["repo"] == "Acme-Corp/widget-api"
            assert result["head_sha"] == "abc123"
            assert result["idempotency_key"] == "Acme-Corp/widget-api:42:abc123"

    assert len(queue.jobs) == 1
    lifecycle = [event for event in logger.events if event["event_name"] == "webhook_lifecycle"]
    assert lifecycle[0]["fields"]["stage"] == "pr_review_started"
    assert lifecycle[1]["fields"]["stage"] == "pr_review_enqueued"
    assert [event["fields"]["stage"] for event in lifecycle[2:]] == [
        "pr_review_started",
        "pr_review_skipped",
        "pr_review_started",
        "pr_review_skipped",
        "pr_review_started",
        "pr_review_skipped",
        "pr_review_started",
        "pr_review_skipped",
        "pr_review_started",
        "pr_review_skipped",
    ]


@pytest.mark.asyncio
async def test_pull_request_handler_allows_new_sha_on_synchronize(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    opened = _build_pull_request_payload(action="opened", head_sha="abc123")
    synced = _build_pull_request_payload(action="synchronize", head_sha="def456")

    first = await handler.handle("pull_request", opened, "delivery-1", workflow_id="wf-1")
    second = await handler.handle("pull_request", synced, "delivery-2", workflow_id="wf-2")

    assert first["status"] == "enqueued"
    assert second["status"] == "enqueued"
    assert len(queue.jobs) == 2
    assert queue.jobs[1].head_sha == "def456"
    assert queue.jobs[1].idempotency_key == "Acme-Corp/widget-api:42:def456"


@pytest.mark.asyncio
async def test_pull_request_handler_ignores_draft_and_non_reviewable_actions(monkeypatch):
    from ace.webhooks import handlers as handler_module

    logger = _StubLogger()
    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )
    monkeypatch.setattr(handler_module, "logger", logger)

    draft_result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="opened", head_sha="abc123", draft=True),
        "delivery-1",
        workflow_id="wf-1",
    )
    closed_result = await handler.handle(
        "pull_request",
        _build_pull_request_payload(action="closed", head_sha="abc123"),
        "delivery-2",
        workflow_id="wf-2",
    )

    assert draft_result == {"status": "ignored", "reason": "draft_pr"}
    assert closed_result == {"status": "ignored", "reason": "action_not_reviewable"}
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_issue_comment_routed_via_work_event(monkeypatch):
    from ace.webhooks import handlers as handlers_module

    called = {"kind": None}

    async def _fake_issue_comment(
        self,
        payload: dict,
        delivery: str | None,
    ) -> dict[str, Any]:
        called["kind"] = "issue_comment"
        return {"status": "patched", "action": "issue_comment"}

    monkeypatch.setattr(
        handlers_module.WebhookHandler,
        "_handle_issue_comment",
        _fake_issue_comment,
    )
    handler = WebhookHandler(
        settings=_DispatchSettingsStub(),
        app_auth=object(),
    )
    result = await handler.handle(
        "issue_comment",
        {"action": "created", "issue": {"number": 1}},
        "delivery-1",
        workflow_id="wf-1",
    )
    assert result == {"status": "patched", "action": "issue_comment"}
    assert called["kind"] == "issue_comment"


@pytest.mark.asyncio
async def test_issues_routed_via_work_event(monkeypatch):
    from ace.webhooks import handlers as handlers_module

    called = {"kind": None}

    async def _fake_issues(
        self,
        payload: dict,
        delivery: str | None,
    ) -> dict[str, Any]:
        called["kind"] = "issues"
        return {"status": "patched", "action": "issues"}

    monkeypatch.setattr(
        handlers_module.WebhookHandler,
        "_handle_issue_event",
        _fake_issues,
    )
    handler = WebhookHandler(
        settings=_DispatchSettingsStub(),
        app_auth=object(),
    )
    result = await handler.handle(
        "issues",
        {"action": "closed", "issue": {"number": 2}},
        "delivery-2",
        workflow_id="wf-2",
    )
    assert result == {"status": "patched", "action": "issues"}
    assert called["kind"] == "issues"


@pytest.mark.asyncio
async def test_pull_request_event_routed_via_work_event(monkeypatch):
    from ace.webhooks import handlers as handlers_module

    called = {"kind": None}

    async def _fake_pull_request(
        self,
        payload: dict,
        delivery: str | None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        called["kind"] = "pull_request"
        return {"status": "patched", "action": "pull_request"}

    monkeypatch.setattr(
        handlers_module.WebhookHandler,
        "_handle_pull_request_event",
        _fake_pull_request,
    )
    handler = WebhookHandler(
        settings=_DispatchSettingsStub(),
        app_auth=object(),
    )
    result = await handler.handle(
        "pull_request",
        {"action": "opened", "number": 3},
        "delivery-3",
        workflow_id="wf-3",
    )
    assert result == {"status": "patched", "action": "pull_request"}
    assert called["kind"] == "pull_request"


@pytest.mark.asyncio
async def test_work_event_input_routes_issue_comment(monkeypatch):
    from ace.webhooks import handlers as handlers_module

    called = {"kind": None}

    async def _fake_issue_comment(
        self,
        payload: dict,
        delivery: str | None,
    ) -> dict[str, Any]:
        called["kind"] = "issue_comment"
        return {"status": "patched", "action": "issue_comment"}

    monkeypatch.setattr(
        handlers_module.WebhookHandler,
        "_handle_issue_comment",
        _fake_issue_comment,
    )
    handler = WebhookHandler(
        settings=_DispatchSettingsStub(),
        app_auth=object(),
    )
    work_event = WorkEvent(
        source="github",
        event_type="issue_comment",
        action="created",
    )
    result = await handler.handle(
        work_event,
        {"action": "created", "issue": {"number": 77}},
        "delivery-work-event",
        workflow_id="wf-work-event",
    )
    assert result == {"status": "patched", "action": "issue_comment"}
    assert called["kind"] == "issue_comment"


@pytest.mark.asyncio
async def test_projects_v2_item_event_routed_via_work_event(monkeypatch):
    from ace.webhooks import handlers as handlers_module

    called = {"kind": None}

    async def _fake_projects_v2_item(self, payload: dict, delivery: str | None) -> dict[str, Any]:
        called["kind"] = "projects_v2_item"
        return {"status": "patched", "action": "projects_v2_item"}

    monkeypatch.setattr(
        handlers_module.WebhookHandler,
        "_handle_projects_v2_item",
        _fake_projects_v2_item,
    )
    handler = WebhookHandler(
        settings=_DispatchSettingsStub(),
        app_auth=object(),
    )
    result = await handler.handle(
        "projects_v2_item",
        {"projects_v2_item": {"node_id": "item-1"}},
        "delivery-4",
        workflow_id="wf-4",
    )
    assert result == {"status": "patched", "action": "projects_v2_item"}
    assert called["kind"] == "projects_v2_item"


@pytest.mark.asyncio
async def test_non_github_work_event_is_ignored_by_handler():
    handler = WebhookHandler(settings=_DispatchSettingsStub(), app_auth=object())
    work_event = WorkEvent(
        source="linear",
        event_type="issue_comment",
    )
    result = await handler.handle(
        work_event,
        {"action": "created", "issue": {"number": 88}},
        "delivery-linear",
    )
    assert result == {"status": "ignored", "reason": "unsupported_event"}


@pytest.mark.asyncio
async def test_unsupported_event_is_ignored_by_router(monkeypatch):
    handler = WebhookHandler(settings=_DispatchSettingsStub(), app_auth=object())
    result = await handler.handle("push", {"repository": {"full_name": "acme/widget"}}, "delivery-5")
    assert result == {"status": "ignored", "reason": "unsupported_event"}


def test_webhook_worker_enqueues_pr_review_job(monkeypatch):
    from ace.webhooks import app as webhook_app

    queue = _StubPRReviewQueue()
    store = InMemoryPRReviewJobStore()
    handler = WebhookHandler(
        settings=_SettingsStub(),
        app_auth=object(),
        pr_review_queue=queue,
        pr_review_store=store,
    )

    old_handler = webhook_app._handler
    old_notifier = webhook_app._notifier
    old_settings = webhook_app._settings
    old_repo_gcp_mapping = webhook_app._repo_gcp_mapping
    webhook_app._handler = handler
    webhook_app._notifier = None
    webhook_app._settings = _WorkerSettingsStub()
    webhook_app._repo_gcp_mapping = {"acme-corp/widget-api": "widget-prod-123456"}

    envelope = {
        "message": {
            "messageId": "1234",
            "data": base64.b64encode(
                json.dumps(
                    {
                        "event": "pull_request",
                        "delivery": "delivery-77",
                        "workflow_id": "wf-77",
                        "queued_at": "2026-03-12T12:00:00+00:00",
                        "correlation": {
                            "delivery_id": "delivery-77",
                            "workflow_id": "wf-77",
                            "issue_key": "Acme-Corp/widget-api#42",
                            "project": "Acme Platform",
                            "target_gcp_project": "widget-prod-123456",
                            "action": "opened",
                        },
                        "payload": _build_pull_request_payload(action="opened", head_sha="abc123"),
                    }
                ).encode("utf-8")
            ).decode("utf-8"),
        }
    }

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/worker", json=envelope)
        assert resp.status_code == 200
        assert resp.json()["result"]["status"] == "enqueued"
        assert resp.json()["result"]["message_id"] == "pr-msg-1"
        assert len(queue.jobs) == 1
        assert queue.jobs[0].workflow_id == "wf-77"
    finally:
        webhook_app._handler = old_handler
        webhook_app._notifier = old_notifier
        webhook_app._settings = old_settings
        webhook_app._repo_gcp_mapping = old_repo_gcp_mapping


def _build_pr_review_worker_envelope(
    *,
    message_id: str = "pr-review-1",
    workflow_id: str = "wf-77",
    head_sha: str = "abc123",
    base_ref: str = "qa",
    installation_id: int = 999,
    action: str = "opened",
    delivery_id: str = "delivery-77",
) -> dict[str, Any]:
    return {
        "message": {
            "messageId": message_id,
            "data": base64.b64encode(
                json.dumps(
                    {
                        "schema_version": "1",
                        "job": {
                            "repo_owner": "Acme-Corp",
                            "repo_name": "widget-api",
                            "pr_number": 42,
                            "head_sha": head_sha,
                            "base_ref": base_ref,
                            "action": action,
                            "workflow_id": workflow_id,
                            "idempotency_key": f"Acme-Corp/widget-api:42:{head_sha}",
                            "installation_id": installation_id,
                            "delivery_id": delivery_id,
                            "project": "Acme Platform",
                            "target_gcp_project": "widget-prod-123456",
                            "queued_at": "2026-03-12T12:00:00+00:00",
                        },
                    }
                ).encode("utf-8")
            ).decode("utf-8"),
        }
    }


def _install_pr_review_runtime_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    openai_verdicts: list[str],
    claude_verdicts: list[str],
    settings: _WorkerSettingsStub,
    merge_result: dict[str, Any],
    call_log: dict[str, int],
) -> tuple[InMemoryPRReviewSessionStore, type[Any], type[Any]]:
    import ace.pr_review.runtime as runtime_module

    async def _fake_installation_token(_installation_id: int) -> Any:
        assert _installation_id == 999
        return type("InstallToken", (), {"token": "test-token"})()

    class _AppAuth:
        async def get_installation_token(self, installation_id: int) -> Any:
            return await _fake_installation_token(installation_id)

    class _FakeGitHubAPIClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self) -> "_FakeGitHubAPIClient":
            return self

        async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
            return None

    openai_calls: dict[str, int] = {"count": 0}
    claude_calls: dict[str, int] = {"count": 0}

    async def _fake_gather_pr_context(*_args: Any, **_kwargs: Any) -> PRContext:
        call_log["context_calls"] += 1
        return _build_pr_context(base_branch=settings.pr_review_target_branch)

    async def _fake_openai(*_args: Any, **_kwargs: Any) -> str:
        index = openai_calls["count"]
        openai_calls["count"] += 1
        call_log["openai_calls"] += 1
        verdict = openai_verdicts[min(index, len(openai_verdicts) - 1)]
        return _build_pr_review_verdict_payload(
            reviewer_model=settings.pr_review_codex_model,
            verdict=verdict,
            round_number=index + 1,
        )

    async def _fake_claude(*_args: Any, **_kwargs: Any) -> str:
        index = claude_calls["count"]
        claude_calls["count"] += 1
        call_log["claude_calls"] += 1
        verdict = claude_verdicts[min(index, len(claude_verdicts) - 1)]
        return _build_pr_review_verdict_payload(
            reviewer_model=settings.pr_review_claude_model,
            verdict=verdict,
            round_number=index + 1,
        )

    async def _fake_submit_pr_approvals(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        call_log["approvals"] += 1
        return {"codex": {"id": "review-codex"}, "claude": {"id": "review-claude"}}

    async def _fake_merge_pr_to_qa(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        call_log["merges"] += 1
        return merge_result

    async def _fake_post_review_summary_comment(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        call_log["summaries"] += 1
        return {"posted": True}

    store = InMemoryPRReviewSessionStore()

    monkeypatch.setattr(runtime_module, "gather_pr_context", _fake_gather_pr_context)
    monkeypatch.setattr(runtime_module, "call_openai", _fake_openai)
    monkeypatch.setattr(runtime_module, "call_claude", _fake_claude)
    monkeypatch.setattr(runtime_module, "submit_pr_approvals", _fake_submit_pr_approvals)
    monkeypatch.setattr(runtime_module, "merge_pr_to_qa", _fake_merge_pr_to_qa)
    monkeypatch.setattr(runtime_module, "post_review_summary_comment", _fake_post_review_summary_comment)
    return store, _AppAuth, _FakeGitHubAPIClient


def test_internal_pubsub_pr_review_worker_runs_runtime_with_live_path(monkeypatch: pytest.MonkeyPatch):
    from ace.webhooks import app as webhook_app

    settings = _WorkerSettingsStub()
    call_log: dict[str, int] = {
        "openai_calls": 0,
        "claude_calls": 0,
        "approvals": 0,
        "merges": 0,
        "summaries": 0,
        "context_calls": 0,
    }

    old_pr_review_session_store = webhook_app._pr_review_session_store
    old_get_pr_review_session_store = webhook_app._get_pr_review_session_store
    old_get_pr_review_app_auth = webhook_app._get_pr_review_app_auth
    old_settings = webhook_app._settings
    old_api_client = webhook_app.GitHubAPIClient
    old_notifier = webhook_app._notifier

    store, app_auth_cls, api_client_cls = _install_pr_review_runtime_stubs(
        monkeypatch,
        openai_verdicts=["approve", "approve"],
        claude_verdicts=["approve", "approve"],
        settings=settings,
        merge_result={"merged": True, "reason": "merged"},
        call_log=call_log,
    )
    webhook_app._get_pr_review_session_store = lambda: store
    webhook_app._pr_review_session_store = None
    webhook_app._get_pr_review_app_auth = lambda: app_auth_cls()
    webhook_app._settings = settings
    webhook_app.GitHubAPIClient = api_client_cls
    webhook_app._notifier = None

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/pr-review", json=_build_pr_review_worker_envelope())
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["result"]["status"] == PRReviewStatus.CONSENSUS_APPROVE
        assert payload["result"]["merged"] is True
        assert payload["session_id"] == "Acme-Corp/widget-api:42:abc123"
        assert call_log["openai_calls"] == 1
        assert call_log["claude_calls"] == 1
        assert call_log["approvals"] == 1
        assert call_log["merges"] == 1
        assert call_log["summaries"] == 1
    finally:
        webhook_app._pr_review_session_store = old_pr_review_session_store
        webhook_app._get_pr_review_session_store = old_get_pr_review_session_store
        webhook_app._get_pr_review_app_auth = old_get_pr_review_app_auth
        webhook_app._settings = old_settings
        webhook_app.GitHubAPIClient = old_api_client
        webhook_app._notifier = old_notifier
        webhook_app._pr_review_session_store = None


def test_internal_pubsub_pr_review_worker_rejects_when_disagreed(monkeypatch: pytest.MonkeyPatch):
    from ace.webhooks import app as webhook_app

    settings = _WorkerSettingsStub()
    call_log: dict[str, int] = {
        "openai_calls": 0,
        "claude_calls": 0,
        "approvals": 0,
        "merges": 0,
        "summaries": 0,
        "context_calls": 0,
    }

    old_pr_review_session_store = webhook_app._pr_review_session_store
    old_get_pr_review_session_store = webhook_app._get_pr_review_session_store
    old_get_pr_review_app_auth = webhook_app._get_pr_review_app_auth
    old_settings = webhook_app._settings
    old_api_client = webhook_app.GitHubAPIClient
    old_notifier = webhook_app._notifier

    store, app_auth_cls, api_client_cls = _install_pr_review_runtime_stubs(
        monkeypatch,
        openai_verdicts=["approve", "approve"],
        claude_verdicts=["request_changes", "request_changes"],
        settings=settings,
        merge_result={"merged": False, "reason": "consensus-reject"},
        call_log=call_log,
    )
    webhook_app._get_pr_review_session_store = lambda: store
    webhook_app._pr_review_session_store = None
    webhook_app._get_pr_review_app_auth = lambda: app_auth_cls()
    webhook_app._settings = settings
    webhook_app.GitHubAPIClient = api_client_cls
    webhook_app._notifier = None

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/pr-review", json=_build_pr_review_worker_envelope())
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["result"]["status"] == PRReviewStatus.CONSENSUS_REJECT
        assert payload["result"]["merged"] is False
        assert call_log["openai_calls"] == 2
        assert call_log["claude_calls"] == 2
        assert call_log["approvals"] == 0
        assert call_log["merges"] == 0
        assert call_log["summaries"] == 0
    finally:
        webhook_app._pr_review_session_store = old_pr_review_session_store
        webhook_app._get_pr_review_session_store = old_get_pr_review_session_store
        webhook_app._get_pr_review_app_auth = old_get_pr_review_app_auth
        webhook_app._settings = old_settings
        webhook_app.GitHubAPIClient = old_api_client
        webhook_app._notifier = old_notifier
        webhook_app._pr_review_session_store = None


def test_internal_pubsub_pr_review_worker_rejects_when_checks_fail(monkeypatch: pytest.MonkeyPatch):
    from ace.webhooks import app as webhook_app

    settings = _WorkerSettingsStub()
    settings.pr_review_require_checks = True
    call_log: dict[str, int] = {
        "openai_calls": 0,
        "claude_calls": 0,
        "approvals": 0,
        "merges": 0,
        "summaries": 0,
        "context_calls": 0,
    }

    old_pr_review_session_store = webhook_app._pr_review_session_store
    old_get_pr_review_session_store = webhook_app._get_pr_review_session_store
    old_get_pr_review_app_auth = webhook_app._get_pr_review_app_auth
    old_settings = webhook_app._settings
    old_api_client = webhook_app.GitHubAPIClient
    old_notifier = webhook_app._notifier

    store, app_auth_cls, api_client_cls = _install_pr_review_runtime_stubs(
        monkeypatch,
        openai_verdicts=["approve", "approve"],
        claude_verdicts=["approve", "approve"],
        settings=settings,
        merge_result={"merged": False, "reason": "pending"},
        call_log=call_log,
    )
    webhook_app._get_pr_review_session_store = lambda: store
    webhook_app._pr_review_session_store = None
    webhook_app._get_pr_review_app_auth = lambda: app_auth_cls()
    webhook_app._settings = settings
    webhook_app.GitHubAPIClient = api_client_cls
    webhook_app._notifier = None

    try:
        client = TestClient(webhook_app.app)
        resp = client.post("/internal/pubsub/pr-review", json=_build_pr_review_worker_envelope())
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["result"]["status"] == PRReviewStatus.CONSENSUS_APPROVE
        assert payload["result"]["merged"] is False
        assert call_log["approvals"] == 1
        assert call_log["merges"] == 1
        assert call_log["summaries"] == 1
    finally:
        webhook_app._pr_review_session_store = old_pr_review_session_store
        webhook_app._get_pr_review_session_store = old_get_pr_review_session_store
        webhook_app._get_pr_review_app_auth = old_get_pr_review_app_auth
        webhook_app._settings = old_settings
        webhook_app.GitHubAPIClient = old_api_client
        webhook_app._notifier = old_notifier
        webhook_app._pr_review_session_store = None


def test_internal_pubsub_pr_review_worker_reuses_session_for_duplicate_pubsub_jobs(
    monkeypatch: pytest.MonkeyPatch,
):
    from ace.webhooks import app as webhook_app

    settings = _WorkerSettingsStub()
    call_log: dict[str, int] = {
        "openai_calls": 0,
        "claude_calls": 0,
        "approvals": 0,
        "merges": 0,
        "summaries": 0,
        "context_calls": 0,
    }

    old_pr_review_session_store = webhook_app._pr_review_session_store
    old_get_pr_review_session_store = webhook_app._get_pr_review_session_store
    old_get_pr_review_app_auth = webhook_app._get_pr_review_app_auth
    old_settings = webhook_app._settings
    old_api_client = webhook_app.GitHubAPIClient
    old_notifier = webhook_app._notifier

    store, app_auth_cls, api_client_cls = _install_pr_review_runtime_stubs(
        monkeypatch,
        openai_verdicts=["approve", "approve"],
        claude_verdicts=["approve", "approve"],
        settings=settings,
        merge_result={"merged": True, "reason": "merged"},
        call_log=call_log,
    )
    webhook_app._get_pr_review_session_store = lambda: store
    webhook_app._pr_review_session_store = None
    webhook_app._get_pr_review_app_auth = lambda: app_auth_cls()
    webhook_app._settings = settings
    webhook_app.GitHubAPIClient = api_client_cls
    webhook_app._notifier = None

    try:
        client = TestClient(webhook_app.app)
        first = client.post("/internal/pubsub/pr-review", json=_build_pr_review_worker_envelope())
        second = client.post("/internal/pubsub/pr-review", json=_build_pr_review_worker_envelope(message_id="pr-review-2"))

        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json()
        second_payload = second.json()
        assert first_payload["result"]["status"] == PRReviewStatus.CONSENSUS_APPROVE
        assert second_payload["result"]["status"] == PRReviewStatus.CONSENSUS_APPROVE
        assert second_payload["result"]["merged"] is True
        assert first_payload["result"]["merged"] is True
        assert call_log["openai_calls"] == 1
        assert call_log["claude_calls"] == 1
        assert call_log["approvals"] == 1
        assert call_log["merges"] == 1
        assert call_log["summaries"] == 1
    finally:
        webhook_app._pr_review_session_store = old_pr_review_session_store
        webhook_app._get_pr_review_session_store = old_get_pr_review_session_store
        webhook_app._get_pr_review_app_auth = old_get_pr_review_app_auth
        webhook_app._settings = old_settings
        webhook_app.GitHubAPIClient = old_api_client
        webhook_app._notifier = old_notifier
        webhook_app._pr_review_session_store = None
