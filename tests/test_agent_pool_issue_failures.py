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


class _FakeMcpClient:
    instances: list["_FakeMcpClient"] = []

    def __init__(self, url: str) -> None:
        self.url = url
        self.call_history: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.call_history.append((name, arguments))
        if name == "list_ready_remote_items":
            return {
                "result": [
                    {
                        "number": 101,
                        "title": "Ready issue",
                        "labels": ["agent:remote"],
                        "repo_owner": "acme",
                        "repo_name": "backend",
                    }
                ]
            }
        if name == "list_issue_blockers":
            return {
                "result": [
                    {
                        "number": 88,
                        "title": "Blocked issue",
                        "state": "open",
                        "repo_owner": "acme",
                        "repo_name": "backend",
                    }
                ]
            }
        return {"result": []}

    async def close(self) -> None:
        self.closed = True


class _FakeApiClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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


@pytest.mark.asyncio
async def test_hydrate_issues_runs_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )

    pool = AgentPool(target=AgentTarget.REMOTE)
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def _hydrate_with_delay(issue: object) -> object:
        nonlocal active, max_active
        async with lock:
            active += 1
            if active > max_active:
                max_active = active
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        return issue

    monkeypatch.setattr(pool, "_hydrate_issue", _hydrate_with_delay)

    issues = [
        _issue(number=1, owner="acme", repo="frontend", title="First"),
        _issue(number=2, owner="acme", repo="frontend", title="Second"),
        _issue(number=3, owner="acme", repo="frontend", title="Third"),
    ]

    await pool._hydrate_issues(issues)

    assert max_active > 1


@pytest.mark.asyncio
async def test_hydrate_issues_preserves_other_issues_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )

    pool = AgentPool(target=AgentTarget.REMOTE)

    async def _hydrate_with_error(issue: object) -> object:
        if issue.number == 2:
            raise RuntimeError("hydrate failed for issue 2")
        return issue

    monkeypatch.setattr(pool, "_hydrate_issue", _hydrate_with_error)

    issues = [
        _issue(number=1, owner="acme", repo="frontend", title="First"),
        _issue(number=2, owner="acme", repo="frontend", title="Second"),
        _issue(number=3, owner="acme", repo="frontend", title="Third"),
    ]

    result = await pool._hydrate_issues(issues)

    assert len(result) == len(issues)
    assert result[0].number == 1
    assert result[1].number == 2
    assert result[2].number == 3


@pytest.mark.asyncio
async def test_fetching_via_appforge_mcp_reuses_single_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _PoolSettings()
    settings.appforge_mcp_url = "https://example.com/mcp"
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: settings)
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )
    monkeypatch.setattr("ace.runners.agent_pool.McpClient", _FakeMcpClient)

    pool = AgentPool(target=AgentTarget.REMOTE)
    _FakeMcpClient.instances.clear()

    ready = await pool._fetch_ready_issues_via_mcp()
    issue = _issue(number=11, owner="acme", repo="backend", title="Need blockers")
    blockers = await pool._fetch_blockers_via_appforge_mcp(issue)

    assert len(ready) == 1
    assert len(blockers) == 1
    assert ready[0].number == 101
    assert blockers[0].number == 88
    assert len(_FakeMcpClient.instances) == 1
    assert len(_FakeMcpClient.instances[0].call_history) == 2
    assert _FakeMcpClient.instances[0].call_history[0][0] == "list_ready_remote_items"
    assert _FakeMcpClient.instances[0].call_history[1][0] == "list_issue_blockers"
    assert pool._mcp_url == "https://example.com/mcp"


@pytest.mark.asyncio
async def test_pool_stop_closes_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _PoolSettings()
    settings.appforge_mcp_url = "https://example.com/mcp"
    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: settings)
    monkeypatch.setattr(
        "ace.runners.agent_pool.resolve_github_token",
        lambda _: "test-github-token",
    )
    monkeypatch.setattr("ace.runners.agent_pool.McpClient", _FakeMcpClient)

    pool = AgentPool(target=AgentTarget.REMOTE)
    api_client = _FakeApiClient()
    pool._api_client = api_client
    mcp_client = _FakeMcpClient("https://example.com/mcp")
    pool._mcp_client = mcp_client
    pool._mcp_url = "https://example.com/mcp"

    pool.stop()
    await asyncio.sleep(0)

    assert api_client.closed is True
    assert mcp_client.closed is True
    assert pool._api_client is None
    assert pool._mcp_client is None
