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


def test_build_plan_artifacts_from_scout_reports() -> None:
    session = PlanningSession(
        project_slug="example-project",
        request_text="Plan the release",
    )
    reports = [
        scouts.ScoutReport(
            repo="repo-one",
            summary="Repository report",
            entrypoints=["app.py"],
            risks=["No tests found"],
            work_items=["Add integration coverage"],
        ),
        scouts.ScoutReport(
            repo="repo-two",
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
    artifacts = scouts.build_planning_artifacts(
        session=session,
        project=project,
        scout_reports=reports,
    )
    assert "repo-one" in artifacts.plan_markdown
    issues_payload = json.loads(artifacts.issues_json)
    assert issues_payload["total"] == 2
    assert issues_payload["issues"][0]["repo"] == "repo-one"
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

    artifact_rows = await planning_routes._default_plan_pipeline(session)
    assert artifact_rows[0][0] == PlanningArtifactType.PLAN_MARKDOWN
    assert artifact_rows[1][0] == PlanningArtifactType.ISSUES_JSON
    assert artifact_rows[2][0] == PlanningArtifactType.DEPENDENCIES_MMD
    assert artifact_store.writes == [
        (session.id, "PLAN.md"),
        (session.id, "ISSUES.json"),
        (session.id, "DEPENDENCIES.mmd"),
    ]
