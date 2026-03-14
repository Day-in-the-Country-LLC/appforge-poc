"""ManagerAgent project coordinator tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ace.agents import manager_agent as manager_agent_module
from ace.agents.manager_agent import ManagerAgent
from ace.github.issue_queue import Issue
from ace.planning.store_firestore import InMemoryPlanningStore


def _issue(
    *,
    number: int,
    owner: str,
    repo: str,
    title: str,
    labels: list[str] | None = None,
) -> Issue:
    return Issue(
        number=number,
        title=title,
        body="",
        labels=labels or [],
        assignee=None,
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        html_url="",
        repo_owner=owner,
        repo_name=repo,
    )


class _ManagerSettings(SimpleNamespace):
    openai_api_key = "openai"
    github_token = "github"
    github_org = "Day-in-the-Country-LLC"
    github_project_name = "appforge-poc"
    planning_store_backend = "memory"
    manager_agent_model = "gpt-5.1-codex-mini"
    codex_model = "gpt-5.1-codex-mini"
    manager_skill_path = ""
    manager_agent_enabled = True
    manager_agent_tool_loop_enabled = False
    manager_agent_tool_loop_max_steps = 6
    agent_project_session_key = "coordinator-session"
    gcp_project_id = ""
    github_remote_agent_label = "agent:remote"
    github_local_agent_label = "agent:local"


async def _stub_openai_call(
    prompt: str,  # noqa: ARG001
    model: str,  # noqa: ARG001
    api_key: str,  # noqa: ARG001
    max_tokens: int,  # noqa: ARG001
    *,
    trace_name: str = "manager_order_work_items",  # noqa: ARG001
    metadata: dict | None = None,  # noqa: ARG001
    reasoning_effort: str | None = None,  # noqa: ARG001
) -> str:
    del prompt, model, api_key, max_tokens, trace_name, metadata, reasoning_effort
    return '["issue:acme/backend#101","issue:acme/frontend#77"]'


@pytest.mark.asyncio
async def test_build_project_plan_persists_coordinator_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")
    monkeypatch.setattr(manager_agent_module, "call_openai", _stub_openai_call)

    store = InMemoryPlanningStore()
    manager = ManagerAgent(planning_store=store)
    in_progress = [
        _issue(
            number=77,
            owner="acme",
            repo="backend",
            title="backend progress",
            labels=["agent:remote"],
        )
    ]
    ready = [
        _issue(
            number=101,
            owner="acme",
            repo="frontend",
            title="frontend ready",
            labels=["agent:remote"],
        )
    ]

    ordered, context = await manager.build_project_plan(
        in_progress,
        ready,
        project_slug="appforge-app",
    )

    assert [issue.number for issue, _ in ordered] == [101, 77]
    assert list(context.keys()) == [
        "issue:acme/frontend#101",
        "issue:acme/backend#77",
    ]
    assert context["issue:acme/frontend#101"]["category"] == "ready"
    assert context["issue:acme/backend#77"]["category"] == "in_progress"

    session = await store.get_session("coordinator:appforge-app")
    assert session is not None
    raw_state = session.intake_state["coordinator"]
    assert raw_state["issue_order"] == [
        "issue:acme/frontend#101",
        "issue:acme/backend#77",
    ]
    assert raw_state["category_counts"] == {"in_progress": 1, "ready": 1, "total": 2}
    assert isinstance(raw_state["project_repositories"], list)
    assert raw_state["project_repositories"] == ["acme/backend", "acme/frontend"]

    events, _ = await store.get_events(session.id)
    assert [event.event_type for event in events] == [
        "coordinator_session_created",
        "coordinator_plan_updated",
    ]


@pytest.mark.asyncio
async def test_build_issue_context_pack_reuses_existing_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")
    monkeypatch.setattr(manager_agent_module, "call_openai", _stub_openai_call)

    store = InMemoryPlanningStore()
    manager = ManagerAgent(planning_store=store)
    issue = _issue(
        number=77,
        owner="acme",
        repo="backend",
        title="backend progress",
        labels=["agent:remote"],
    )
    other = _issue(
        number=101,
        owner="acme",
        repo="frontend",
        title="frontend ready",
        labels=["agent:remote"],
    )

    _, _ = await manager.build_project_plan([issue], [other], project_slug="appforge-app")
    context = await manager.build_issue_context_pack(other, project_slug="appforge-app")

    assert context["issue_key"] == "issue:acme/frontend#101"
    assert context["project_session_key"] == "coordinator:appforge-app"
    assert context["project_slug"] == "appforge-app"
    assert context["issue_count_by_category"] == {"in_progress": 1, "ready": 1, "total": 2}
    assert context["total"] == 2
    assert context["preceding_issues"] == []
    assert context["following_issues"] == [
        "issue:acme/frontend#101",
        "issue:acme/backend#77",
    ]
