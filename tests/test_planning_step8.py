"""Step 8 planning scout and artifact synthesis tests."""

from __future__ import annotations

import base64
import json

import pytest

from ace.config.settings import get_settings, set_settings_overrides
from ace.planning import scouts
from ace.planning.models import PlanningArtifactType, PlanningSession


@pytest.mark.asyncio
async def test_load_project_registry_falls_back_to_local_file() -> None:
    set_settings_overrides(
        gcp_project_id="",
        planning_artifacts_bucket="",
        planning_store_backend="memory",
        planning_pubsub_topic="projects/test/topics/appforge-planner-jobs",
        webhook_service_role="all",
        github_token="",
    )
    registry = await scouts.load_project_registry(
        "example-project",
        settings=get_settings(),
    )
    assert registry.project_slug == "example-project"
    assert len(registry.repos) == 2
    assert registry.repos[0].owner == "your-org"


@pytest.mark.asyncio
async def test_build_plan_artifacts_from_scout_reports(monkeypatch) -> None:
    session = PlanningSession(
        project_slug="example-project",
        request_text="Plan the release",
    )
    reports = [
        scouts.ScoutReport(
            repo="team/repo-one",
            summary="Repository report",
            entrypoints=["app.py"],
            risks=["No tests found"],
            work_items=["Add integration coverage"],
        ),
        scouts.ScoutReport(
            repo="team/repo-two",
            summary="Worker report",
            entrypoints=["main.py"],
            risks=[],
            work_items=["Verify startup path"],
        ),
    ]
    project = scouts._project_registry_from_payload(
        {
            "project_slug": "example-project",
            "repos": [
                {"owner": "team", "name": "repo-one"},
                {"owner": "team", "name": "repo-two"},
            ],
        },
        source="tests",
    )

    calls: list[str] = []

    async def fake_call_openai(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: int,
        *,
        trace_name: str = "openai_call",
        metadata: dict | None = None,  # noqa: ARG001
        reasoning_effort: str | None = None,
    ) -> str:
        del prompt, metadata
        assert model == "gpt-5.2-codex"
        assert api_key == "test-openai-key"
        assert reasoning_effort == "high"
        calls.append(trace_name)
        if trace_name in {
            "planning_synthesis_plan_agent",
            "planning_synthesis_plan_agent_collab_round_1",
        }:
            assert max_tokens == 4000
            return (
                "# Implementation Plan\n\n"
                "## Objective\nShip the release.\n\n"
                "## Scope\n- team/repo-one\n- team/repo-two\n\n"
                "## Repo Findings\n- app.py and main.py are entrypoints.\n\n"
                "## Execution Phases\n1. Prepare changes.\n\n"
                "## Risks and Mitigations\n- Missing tests.\n\n"
                "## Validation Strategy\n- Run targeted tests."
            )
        if trace_name in {
            "planning_synthesis_issue_agent",
            "planning_synthesis_issue_agent_collab_round_1",
        }:
            assert max_tokens == 12000
            return json.dumps(
                {
                    "issues": [
                        {
                            "id": "ISSUE-001",
                            "repo": "team/repo-one",
                            "title": "Add integration coverage",
                            "description": "Cover release execution path in app.py.",
                            "priority": "high",
                            "depends_on": [],
                        },
                        {
                            "id": "ISSUE-002",
                            "repo": "team/repo-two",
                            "title": "Verify startup path",
                            "description": "Validate worker startup and release checks.",
                            "priority": "medium",
                            "depends_on": ["ISSUE-001"],
                        },
                    ]
                }
            )
        if trace_name in {
            "planning_synthesis_dependency_agent",
            "planning_synthesis_dependency_agent_collab_round_1",
        }:
            assert max_tokens == 2000
            return json.dumps(
                {
                    "dependencies_mmd": (
                        "flowchart TD\n"
                        "    ISSUE_001[ISSUE-001]\n"
                        "    ISSUE_002[ISSUE-002]\n"
                        "    ISSUE_001 --> ISSUE_002"
                    )
                }
            )
        if trace_name == "planning_synthesis_controller_agent_round_1":
            assert max_tokens == 2000
            return json.dumps(
                {
                    "decision": "continue",
                    "feedback": "Tighten issue text and ensure dependency alignment.",
                }
            )
        raise AssertionError(f"unexpected trace_name: {trace_name}")

    monkeypatch.setattr(scouts, "call_openai", fake_call_openai)

    artifacts = await scouts.build_planning_artifacts(
        session=session,
        project=project,
        scout_reports=reports,
        openai_api_key="test-openai-key",
        model="gpt-5.2-codex",
        plan_max_tokens=4000,
        issue_max_tokens=12000,
        dependencies_max_tokens=2000,
        controller_max_tokens=2000,
        reasoning_effort="high",
        max_turns_per_agent=2,
    )
    assert calls == [
        "planning_synthesis_plan_agent",
        "planning_synthesis_issue_agent",
        "planning_synthesis_dependency_agent",
        "planning_synthesis_controller_agent_round_1",
        "planning_synthesis_plan_agent_collab_round_1",
        "planning_synthesis_issue_agent_collab_round_1",
        "planning_synthesis_dependency_agent_collab_round_1",
    ]
    assert "team/repo-one" in artifacts.plan_markdown
    issues_payload = json.loads(artifacts.issues_json)
    assert issues_payload["total"] == 2
    assert issues_payload["issues"][0]["repo"] == "team/repo-one"
    assert "flowchart TD" in artifacts.dependencies_mmd


class _FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token
        del token

    async def rest_get(self, endpoint: str, params: dict[str, str] | None = None):
        del params
        if endpoint.endswith("/repos/owner-one/repo-one"):
            return {"default_branch": "main"}
        if endpoint.endswith("/repos/owner-two/repo-two"):
            return {"default_branch": "main"}
        if endpoint.endswith("/git/trees/main") or endpoint.endswith("/git/trees/main/"):
            return {
                "tree": [
                    {"path": "README.md", "type": "blob"},
                    {"path": "app.py", "type": "blob"},
                    {"path": "src/main.go", "type": "blob"},
                ]
            }
        if endpoint.endswith("/repos/owner-one/repo-one/contents/readme.md"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"readme").decode("ascii"),
            }
        if endpoint.endswith("/repos/owner-two/repo-two/contents/readme.md"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"testing plan").decode("ascii"),
            }
        return {"encoding": "base64", "content": base64.b64encode(b"{}").decode("ascii")}

    async def close(self) -> None:
        return None


class _StubArtifactStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.writes_with_content: list[tuple[str, str, str]] = []

    async def write_artifact(
        self,
        session_id: str,
        filename: str,
        content: str,
        *,
        content_type: str = "text/plain",
    ) -> str:
        self.writes.append((session_id, filename))
        self.writes_with_content.append((session_id, filename, content))
        del content_type
        return f"https://example.test/{session_id}/{filename}"

    async def write_plan_markdown(self, session_id: str, content: str) -> str:
        return await self.write_artifact(
            session_id=session_id,
            filename="PLAN.md",
            content=content,
            content_type="text/markdown",
        )

    async def write_issues_json(self, session_id: str, content: str) -> str:
        return await self.write_artifact(
            session_id=session_id,
            filename="ISSUES.json",
            content=content,
            content_type="application/json",
        )

    async def write_dependencies_mmd(self, session_id: str, content: str) -> str:
        return await self.write_artifact(
            session_id=session_id,
            filename="DEPENDENCIES.mmd",
            content=content,
            content_type="text/plain",
        )


@pytest.mark.asyncio
async def test_default_planner_pipeline_uses_scout_reports(monkeypatch) -> None:
    import ace.planning.routes as planning_routes

    set_settings_overrides(
        secrets_backend="env",
        github_token="test-planning-github-token",
        openai_api_key="test-openai-key",
        planning_synthesis_model="gpt-5.2-codex",
        planning_synthesis_plan_max_tokens=4000,
        planning_synthesis_issue_max_tokens=12000,
        planning_synthesis_dependencies_max_tokens=2000,
        planning_synthesis_controller_max_tokens=2000,
        planning_synthesis_reasoning_effort="high",
        planning_synthesis_max_turns_per_agent=2,
    )

    artifact_store = _StubArtifactStore()
    planning_routes._planner_pipeline = None
    planning_routes._artifact_store = artifact_store
    monkeypatch.setattr(
        "ace.planning.scouts.GitHubAPIClient",
        _FakeGitHubClient,
    )

    session = PlanningSession(
        project_slug="step8-project",
        request_text="Plan a cross-repo migration",
        intake_state={
            "planning_context": (
                "Conversation transcript:\n"
                "- user: Success means staging deploy + CI green.\n"
                "- user: Scope includes owner-one/repo-one and owner-two/repo-two."
            )
        },
    )
    project = scouts._project_registry_from_payload(
        {
            "project_slug": "step8-project",
            "repos": [
                {"owner": "owner-one", "name": "repo-one"},
                {"owner": "owner-two", "name": "repo-two"},
            ],
        },
        source="tests",
    )

    async def _load_project(_: str, settings: object | None = None) -> object:
        del settings
        return project

    monkeypatch.setattr(
        planning_routes,
        "load_project_registry",
        _load_project,
    )

    calls: list[str] = []

    async def fake_call_openai(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: int,
        *,
        trace_name: str = "openai_call",
        metadata: dict | None = None,  # noqa: ARG001
        reasoning_effort: str | None = None,
    ) -> str:
        calls.append(trace_name)
        assert model == "gpt-5.2-codex"
        assert api_key == "test-openai-key"
        assert reasoning_effort == "high"
        if trace_name in {
            "planning_synthesis_plan_agent",
            "planning_synthesis_plan_agent_collab_round_1",
        }:
            assert max_tokens == 4000
            assert "Intake context:" in prompt
            return (
                "# Implementation Plan\n\n"
                "## Objective\nDeliver migration safely.\n\n"
                "## Scope\n- owner-one/repo-one\n- owner-two/repo-two\n\n"
                "## Repo Findings\n- app.py and src/main.go are relevant.\n\n"
                "## Execution Phases\n1. Migrate one repo at a time.\n\n"
                "## Risks and Mitigations\n- CI instability.\n\n"
                "## Validation Strategy\n- Staging deploy and CI green."
            )
        if trace_name in {
            "planning_synthesis_issue_agent",
            "planning_synthesis_issue_agent_collab_round_1",
        }:
            assert max_tokens == 12000
            return json.dumps(
                {
                    "issues": [
                        {
                            "id": "ISSUE-001",
                            "repo": "owner-one/repo-one",
                            "title": "Prepare migration scaffold",
                            "description": "Set up initial migration scaffolding.",
                            "priority": "high",
                            "depends_on": [],
                        }
                    ]
                }
            )
        if trace_name in {
            "planning_synthesis_dependency_agent",
            "planning_synthesis_dependency_agent_collab_round_1",
        }:
            assert max_tokens == 2000
            return json.dumps({"dependencies_mmd": "flowchart TD\n    ISSUE_001[ISSUE-001]"})
        if trace_name == "planning_synthesis_controller_agent_round_1":
            assert max_tokens == 2000
            return json.dumps(
                {
                    "decision": "continue",
                    "feedback": "Ensure issues and dependency graph remain consistent.",
                }
            )
        raise AssertionError(f"unexpected trace_name: {trace_name}")

    monkeypatch.setattr(scouts, "call_openai", fake_call_openai)

    artifact_rows, artifact_payloads = await planning_routes._default_plan_pipeline(session)
    assert artifact_rows[0][0] == PlanningArtifactType.PLAN_MARKDOWN
    assert artifact_rows[1][0] == PlanningArtifactType.ISSUES_JSON
    assert artifact_rows[2][0] == PlanningArtifactType.DEPENDENCIES_MMD
    assert PlanningArtifactType.PLAN_MARKDOWN in artifact_payloads
    assert PlanningArtifactType.ISSUES_JSON in artifact_payloads
    assert PlanningArtifactType.DEPENDENCIES_MMD in artifact_payloads
    assert "## Objective" in artifact_payloads[PlanningArtifactType.PLAN_MARKDOWN]
    issues_payload = json.loads(artifact_payloads[PlanningArtifactType.ISSUES_JSON])
    assert issues_payload["issues"][0]["repo"] == "owner-one/repo-one"
    assert calls == [
        "planning_synthesis_plan_agent",
        "planning_synthesis_issue_agent",
        "planning_synthesis_dependency_agent",
        "planning_synthesis_controller_agent_round_1",
        "planning_synthesis_plan_agent_collab_round_1",
        "planning_synthesis_issue_agent_collab_round_1",
        "planning_synthesis_dependency_agent_collab_round_1",
    ]
    assert artifact_store.writes == [
        (session.id, "PLAN.md"),
        (session.id, "ISSUES.json"),
        (session.id, "DEPENDENCIES.mmd"),
    ]
