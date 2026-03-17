"""ManagerAgent project coordinator tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ace.agents import manager_agent as manager_agent_module
from ace.agents.manager_agent import ManagerAgent, _safe_parse_int_list
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


class _IssueQueueStub:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    async def get_issue(self, number: int, repo_owner: str, repo_name: str) -> Issue:
        self.calls.append((number, repo_owner, repo_name))
        return _issue(
            number=number,
            owner=repo_owner,
            repo=repo_name,
            title=f"{repo_owner}/{repo_name}#{number}",
            labels=["agent:remote"],
        )


class _ProjectsClientStub:
    def __init__(self) -> None:
        self.blocker_calls: list[tuple[str, str, int]] = []
        self.status_calls: list[tuple[str, int, str, str]] = []

    async def get_issue_blockers(self, repo_owner: str, repo_name: str, number: int) -> list[Issue]:
        self.blocker_calls.append((repo_owner, repo_name, number))
        return []

    async def get_issue_project_status(
        self,
        project_id: str,
        number: int,
        repo_owner: str,
        repo_name: str,
    ) -> str:
        self.status_calls.append((project_id, number, repo_owner, repo_name))
        return "done"

    async def get_org_project_id(self, _org: str, _project: str) -> str:
        return "project-id"


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


@pytest.mark.asyncio
async def test_call_tool_get_issue_validates_number_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")

    manager = ManagerAgent()
    manager._issue_queue = _IssueQueueStub()

    result = await manager._call_tool(
        "get_issue",
        {"repo_owner": "acme", "repo_name": "backend"},
    )
    assert result["error"] == "get_issue tool requires integer field 'number'"


@pytest.mark.asyncio
async def test_call_tool_get_issue_validates_repository_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")

    manager = ManagerAgent()
    manager._issue_queue = _IssueQueueStub()

    result = await manager._call_tool(
        "get_issue",
        {"number": 7, "repo_owner": "acme"},
    )
    assert result["error"] == "get_issue tool requires non-empty string field 'repo_name'"


@pytest.mark.asyncio
async def test_call_tool_list_blockers_validates_number_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")

    manager = ManagerAgent()
    projects_client = _ProjectsClientStub()
    manager._projects_client = projects_client

    result = await manager._call_tool(
        "list_blockers",
        {"repo_owner": "acme", "repo_name": "backend", "number": "not-a-number"},
    )
    assert result["error"] == (
        "list_blockers tool requires integer field 'number', got str"
    )


@pytest.mark.asyncio
async def test_call_tool_get_project_status_validates_number_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_agent_module, "get_settings", lambda: _ManagerSettings())
    monkeypatch.setattr(manager_agent_module, "resolve_github_token", lambda _settings: "github")
    monkeypatch.setattr(manager_agent_module, "resolve_openai_api_key", lambda _settings: "openai")

    manager = ManagerAgent()
    manager._projects_client = _ProjectsClientStub()

    result = await manager._call_tool(
        "get_project_status",
        {"repo_owner": "acme", "repo_name": "backend", "number": None},
    )
    assert result["error"] == "get_project_status tool requires integer field 'number'"


def test_safe_parse_int_list_parses_json_numbers_and_code_fences() -> None:
    assert _safe_parse_int_list('[1, "2", 3, 4.7]') == [1, 2, 3, 4]
    assert _safe_parse_int_list("```json\n[5, \"6\", 7]\n```") == [5, 6, 7]


def test_safe_parse_int_list_ignores_invalid_items_and_bad_payload() -> None:
    assert _safe_parse_int_list("[1, \"bad\", null, true]") == [1]
    assert _safe_parse_int_list("not-json") == []
