"""Coordinator handoff integration tests for AgentPool."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ace.github.issue_queue import Issue
from ace.runners.agent_pool import AgentPool, AgentTarget


def _issue(
    *,
    number: int,
    owner: str,
    repo: str,
    title: str,
) -> Issue:
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
    agent_workspace_root = "/tmp/agent-hq"
    github_project_name = "appforge-poc"


class _FakeCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def build_project_plan(
        self,
        in_progress: list[Issue],
        ready: list[Issue],
        project_slug: str | None = None,
    ) -> tuple[list[tuple[Issue, str]], dict[str, dict[str, object]]]:
        self.calls.append(("build_project_plan", project_slug or ""))
        ordered: list[tuple[Issue, str]] = []
        context: dict[str, dict[str, object]] = {}

        if ready:
            ordered.append((ready[0], "issue:acme/frontend#303"))
            context["issue:acme/frontend#303"] = {
                "issue_key": "issue:acme/frontend#303",
                "project_session_key": "coordinator:appforge-poc",
                "category": "ready",
            }
        if in_progress:
            ordered.append((in_progress[0], "issue:acme/backend#401"))
            context["issue:acme/backend#401"] = {
                "issue_key": "issue:acme/backend#401",
                "project_session_key": "coordinator:appforge-poc",
                "category": "in_progress",
            }
        return ordered, context

    async def build_issue_context_pack(
        self,
        issue: Issue,
        project_slug: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("build_issue_context_pack", project_slug or ""))
        return {
            "issue_key": f"issue:{issue.repo_owner}/{issue.repo_name}#{issue.number}",
            "project_session_key": f"coordinator:{project_slug or 'appforge-poc'}",
            "category": "single",
        }


@pytest.mark.asyncio
async def test_build_work_queue_uses_coordinator_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    in_progress = [_issue(number=401, owner="acme", repo="backend", title="In progress work")]
    ready = [_issue(number=303, owner="acme", repo="frontend", title="Ready work")]

    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr("ace.runners.agent_pool.resolve_github_token", lambda _: "test-github-token")

    pool = AgentPool(target=AgentTarget.REMOTE)

    async def _fetch_in_progress() -> list[Issue]:
        return in_progress

    async def _fetch_ready() -> list[Issue]:
        return ready

    monkeypatch.setattr(pool, "fetch_in_progress_issues", _fetch_in_progress)
    monkeypatch.setattr(pool, "fetch_ready_issues", _fetch_ready)

    coordinator = _FakeCoordinator()
    monkeypatch.setattr(pool, "_get_manager_agent", lambda: coordinator)

    work_queue, counts = await pool._build_work_queue()

    assert counts == {"in_progress": 1, "ready": 1}
    assert [
        issue.number for issue, _ in work_queue
    ] == [303, 401]
    assert [
        key for _, key in work_queue
    ] == [
        "issue:acme/frontend#303",
        "issue:acme/backend#401",
    ]
    assert pool._work_meta_by_key == {
        "issue:acme/backend#401": {
            "issue_key": "issue:acme/backend#401",
            "project_session_key": "coordinator:appforge-poc",
            "category": "in_progress",
        },
        "issue:acme/frontend#303": {
            "issue_key": "issue:acme/frontend#303",
            "project_session_key": "coordinator:appforge-poc",
            "category": "ready",
        },
    }
    assert coordinator.calls == [("build_project_plan", "appforge-poc")]


@pytest.mark.asyncio
async def test_process_issue_builds_single_issue_context(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = _issue(number=303, owner="acme", repo="frontend", title="Manual run")

    monkeypatch.setattr("ace.runners.agent_pool.get_settings", lambda: _PoolSettings())
    monkeypatch.setattr("ace.runners.agent_pool.resolve_github_token", lambda _: "test-github-token")

    pool = AgentPool(target=AgentTarget.REMOTE)

    async def _spawn(_: Issue, work_key: str) -> bool:
        del work_key
        return True

    async def _hydrate_unchanged(raw_issue: Issue) -> Issue:
        return raw_issue

    monkeypatch.setattr(pool, "spawn_agent", _spawn)
    monkeypatch.setattr(pool, "_hydrate_issue", _hydrate_unchanged)

    coordinator = _FakeCoordinator()
    monkeypatch.setattr(pool, "_get_manager_agent", lambda: coordinator)

    result = await pool.process_issue(issue)

    assert result["status"] == "processing"
    assert pool._work_meta_by_key["issue:acme/frontend#303"] == {
        "issue_key": "issue:acme/frontend#303",
        "project_session_key": "coordinator:appforge-poc",
        "category": "single",
    }
