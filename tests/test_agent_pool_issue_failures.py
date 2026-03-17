"""Targeted AgentPool execution failure behavior tests."""

from __future__ import annotations

import asyncio

from datetime import UTC, datetime

import pytest

from ace.github.issue_queue import Issue
from ace.runners.agent_pool import AgentPool, AgentState, AgentTarget


class _PoolSettings:
    github_org = "Day-in-the-Country-LLC"
    github_project_name = "appforge-poc"
    github_ready_status = "Ready"
    github_remote_agent_label = "agent:remote"
    github_local_agent_label = "agent:local"
    github_token = "test-github-token"
    manager_agent_enabled = True
    appforge_mcp_url = ""
    secrets_backend = "env"
    openai_api_key = "test-openai-key"
    claude_api_key = ""
    linear_api_key = ""
    github_token_secret_name = ""
    github_token_secret_version = "latest"
    openai_secret_name = ""
    openai_secret_version = "latest"
    claude_secret_name = ""
    claude_secret_version = "latest"
    linear_api_key_secret_name = ""
    linear_api_key_secret_version = "latest"
    gcp_project_id = ""
    gcp_credentials_path = ""
    appforge_claude_backend = "tmux"
    github_label_name = ""
    agent_workspace_root = "/tmp/agent-hq"
    secret_cache_ttl_seconds = 30
    cleanup_enabled = False
    cleanup_interval_seconds = 300
    cleanup_retention_hours = 72
    cleanup_tmux_enabled = False
    cleanup_tmux_retention_hours = 24
    cleanup_only_done = False
    appforge_graph_backend = "codex"
    appforge_work_items_page_size = 30
    appforge_max_retries = 0
    appforge_backoff_seconds = 0
    appforge_nudge_interval_seconds = 0
    appforge_nudge_timeout_seconds = 0
    appforge_nudge_max_per_run = 0
    appforge_task_wait_timeout_seconds = 0
    appforge_session_timeout_seconds = 0
    appforge_session_poll_interval_seconds = 0
    max_concurrent_agents = 5
    repo_root = "/tmp"


def _issue(*, number: int, owner: str, repo: str, title: str) -> Issue:
    return Issue(
        number=number,
        title=title,
        body="",
        labels=["agent:remote"],
        assignee=None,
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        html_url="",
        repo_owner=owner,
        repo_name=repo,
    )


class _FakeAgentGraph:
    def __init__(self, final_state: dict) -> None:
        self.final_state = final_state

    async def ainvoke(self, _state):
        if isinstance(self.final_state, Exception):
            raise self.final_state
        return self.final_state


class _LogCapture:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def error(self, message: str, **kwargs: str) -> None:  # pragma: no cover
        self.events.append((message, kwargs))


@pytest.mark.asyncio
async def test_failed_agent_result_does_not_stop_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )

    pool = AgentPool(target=AgentTarget.REMOTE)
    monkeypatch.setattr(pool, "_schedule_refill", lambda: None)
    monkeypatch.setattr(
        "ace.runners.agent_pool.get_compiled_graph",
        lambda: _FakeAgentGraph(
            {
                "pr_number": 123,
                "backend": "codex",
                "agent_result": {
                    "status": "failed",
                    "error": "unit-test failure",
                    "output": "agent output",
                },
            },
        ),
    )

    slot = pool.slots[0]
    await pool._run_agent_for_issue(slot, _issue(number=303, owner="acme", repo="frontend", title="Work"))

    assert pool._completed_count == 0
    assert pool._failed_count == 1
    assert pool._session_processed == 1
    assert pool._fatal_error is None
    assert slot.state == AgentState.IDLE
    assert slot.error == "unit-test failure"


@pytest.mark.asyncio
async def test_fatal_pool_error_bubbles_to_fatal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )

    pool = AgentPool(target=AgentTarget.REMOTE)
    monkeypatch.setattr(pool, "_schedule_refill", lambda: None)
    monkeypatch.setattr(
        "ace.runners.agent_pool.get_compiled_graph",
        lambda: _FakeAgentGraph(
            ValueError("❌ ERROR: GitHub token missing from environment")
        ),
    )

    slot = pool.slots[0]
    await pool._run_agent_for_issue(slot, _issue(number=404, owner="acme", repo="frontend", title="Work"))

    assert pool._completed_count == 0
    assert pool._failed_count == 1
    assert pool._session_processed == 1
    assert pool._fatal_error is not None
    assert "GitHub token missing from environment" in pool._fatal_error
    assert slot.state == AgentState.IDLE


@pytest.mark.asyncio
async def test_schedule_refill_logs_errors_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )

    pool = AgentPool(target=AgentTarget.REMOTE)
    pool._running = True

    events: list[str] = []
    error_log = _LogCapture()
    monkeypatch.setattr("ace.runners.agent_pool.logger", error_log)

    async def failing_refill() -> None:
        raise RuntimeError("intentional refill failure")

    async def successful_refill() -> None:
        events.append("refilled")

    monkeypatch.setattr(pool, "_refill_slots", failing_refill)
    pool._schedule_refill()
    await asyncio.sleep(0)

    assert not pool._refill_scheduled
    assert len(error_log.events) == 1
    assert "❌ ERROR: refill_task_failed" in error_log.events[0][0]

    monkeypatch.setattr(pool, "_refill_slots", successful_refill)
    pool._schedule_refill()
    await asyncio.sleep(0)

    assert events == ["refilled"]
