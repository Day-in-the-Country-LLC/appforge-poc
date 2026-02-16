"""Planner worker endpoint tests."""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.planning.models import PlanningArtifactType
from ace.webhooks.app import app

_PLANNER_TOKEN = "test-planning-token"
_PLANNER_AUTH_HEADER = {"Authorization": f"Bearer {_PLANNER_TOKEN}"}


class _StubArtifactStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def write_artifact(
        self,
        session_id: str,
        filename: str,
        content: str,
        *,
        content_type: str,
    ) -> str:
        self.writes.append((session_id, filename, content))
        del content_type
        return f"https://example.test/{session_id}/{filename}"


class _FakeIssueGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.created: list[tuple[str, dict[str, Any]]] = []
        del token

    async def rest_post(self, endpoint: str, json: dict[str, Any]) -> dict[str, Any]:
        self.created.append((endpoint, json))
        repo_path = endpoint.replace("/repos/", "").rsplit("/issues", maxsplit=1)[0]
        return {
            "html_url": f"https://github.com/{repo_path}/issues/{len(self.created)}",
            "number": len(self.created),
        }

    async def close(self) -> None:
        return None


def _pipeline_with_artifacts(
    artifact_store: _StubArtifactStore,
) -> Callable[[Any], Any]:
    async def _pipeline(session: Any) -> list[tuple[PlanningArtifactType, str]]:
        session_id = session.id
        plan_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="PLAN.md",
            content="plan-markdown",
            content_type="text/markdown",
        )
        issues_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="ISSUES.json",
            content='{"issues": []}',
            content_type="application/json",
        )
        dependencies_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="DEPENDENCIES.mmd",
            content="flowchart TD",
            content_type="text/plain",
        )
        return [
            (PlanningArtifactType.PLAN_MARKDOWN, plan_url),
            (PlanningArtifactType.ISSUES_JSON, issues_url),
            (PlanningArtifactType.DEPENDENCIES_MMD, dependencies_url),
        ]

    return _pipeline


def _pipeline_with_artifacts_and_payload(
    artifact_store: _StubArtifactStore,
    issue_payload: str,
) -> Callable[[Any], Any]:
    async def _pipeline(
        session: Any,
    ) -> tuple[
        list[tuple[PlanningArtifactType, str]],
        dict[PlanningArtifactType, str],
    ]:
        session_id = session.id
        plan_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="PLAN.md",
            content="plan-markdown",
            content_type="text/markdown",
        )
        issues_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="ISSUES.json",
            content=issue_payload,
            content_type="application/json",
        )
        dependencies_url = await artifact_store.write_artifact(
            session_id=session_id,
            filename="DEPENDENCIES.mmd",
            content="flowchart TD",
            content_type="text/plain",
        )
        return [
            (PlanningArtifactType.PLAN_MARKDOWN, plan_url),
            (PlanningArtifactType.ISSUES_JSON, issues_url),
            (PlanningArtifactType.DEPENDENCIES_MMD, dependencies_url),
        ], {
            PlanningArtifactType.PLAN_MARKDOWN: "plan-markdown",
            PlanningArtifactType.ISSUES_JSON: issue_payload,
            PlanningArtifactType.DEPENDENCIES_MMD: "flowchart TD",
        }

    return _pipeline


class _FailingArtifactStore:
    async def write_artifact(
        self,
        session_id: str,
        filename: str,
        content: str,
        *,
        content_type: str,
    ) -> str:  # noqa: ARG002
        del session_id, filename, content, content_type
        raise RuntimeError("artifact upload unavailable")


class _StubPlannerQueue:
    async def publish(
        self,
        *,
        session_id: str,
        project_slug: str,
        mode: str,
        created_at: str,
    ) -> str:
        del session_id, project_slug, mode, created_at
        return "message-id-123"


def _prepare_stub_pipeline(
    planning_routes: Any,
    artifact_store: _StubArtifactStore,
) -> None:
    planning_routes._planner_pipeline = _pipeline_with_artifacts(artifact_store)


def _planning_app_client() -> TestClient:
    set_settings_overrides(
        planner_api_token=_PLANNER_TOKEN,
        webhook_service_role="all",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        github_token="test-planning-github-token",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
        planning_review_enabled=False,
        planning_intake_agent_enabled=True,
    )
    return TestClient(app, headers=_PLANNER_AUTH_HEADER)


def _planning_session_ready(
    client: TestClient,
    project_slug: str = "example-project",
    mode: str = "plan_only",
) -> str:
    import ace.planning.routes as planning_routes

    original_request = planning_routes._request_intake_agent_decision
    original_queue = planning_routes._planner_queue

    async def fake_request_intake_agent_decision(
        *,
        session: Any,
        messages: Any,
        state: Any,
        settings: Any,
    ) -> Any:
        del session, messages, state, settings
        return planning_routes._IntakeAgentDecision(
            action="ready_to_plan",
            assistant_message="Intake complete. Click Start planning when ready.",
            repo_question=None,
        )

    planning_routes._request_intake_agent_decision = fake_request_intake_agent_decision
    planning_routes._planner_queue = _StubPlannerQueue()
    try:
        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": project_slug,
                "mode": mode,
                "request_text": "Build a migration plan",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        created_answer = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "content": "Need migration plan and worker execution.",
                "source": "user",
            },
        )
        assert created_answer.status_code == 200

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "running"
        return session_id
    finally:
        planning_routes._request_intake_agent_decision = original_request
        planning_routes._planner_queue = original_queue


def _planner_push_envelope(
    session_id: str,
    mode: str = "plan_only",
) -> dict[str, object]:
    raw = {
        "schema_version": "1",
        "event": "planner.start",
        "payload": {
            "session_id": session_id,
            "project_slug": "example-project",
            "mode": mode,
            "created_at": "2026-02-15T00:00:00Z",
        },
        "queued_at": "2026-02-15T00:00:01Z",
    }
    encoded = base64.b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")
    return {
        "message": {
            "messageId": "plan-worker-1",
            "data": encoded,
            "attributes": {
                "event": "planner.start",
                "session_id": session_id,
                "project_slug": "example-project",
                "mode": mode,
            },
        }
    }


def test_planner_worker_stores_stub_plan() -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        artifact_store = _StubArtifactStore()
        planning_routes._artifact_store = artifact_store
        _prepare_stub_pipeline(planning_routes, artifact_store)

        session_id = _planning_session_ready(client)
        push = _planner_push_envelope(session_id)
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        assert resp.json()["session_id"] == session_id

        assert len(artifact_store.writes) == 3
        assert artifact_store.writes == [
            (session_id, "PLAN.md", "plan-markdown"),
            (session_id, "ISSUES.json", '{"issues": []}'),
            (session_id, "DEPENDENCIES.mmd", "flowchart TD"),
        ]

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "done"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        assert event_payload[-2]["event_type"] == "running"
        assert event_payload[-2]["payload"]["status"].startswith("running")
        assert event_payload[-1]["event_type"] == "done"

        artifacts = client.get(f"/planning/sessions/{session_id}/artifacts")
        assert artifacts.status_code == 200
        artifact_rows = artifacts.json()["artifacts"]
        assert len(artifact_rows) == 3
        assert artifact_rows[0]["artifact_type"] == "plan_md"
        assert artifact_rows[1]["artifact_type"] == "issues_json"
        assert artifact_rows[2]["artifact_type"] == "dependencies_mmd"


def test_planner_worker_creates_github_issues_when_requested(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        artifact_store = _StubArtifactStore()
        planning_routes._artifact_store = artifact_store
        issues_payload = json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-001",
                        "repo": "owner-one/repo-one",
                        "title": "Prepare scaffold",
                        "description": "Plan and wire scaffold",
                    },
                    {
                        "id": "ISSUE-002",
                        "repo": "owner-two/repo-two",
                        "title": "Implement feature",
                        "description": "Build feature from scaffold",
                        "depends_on": ["ISSUE-001"],
                    },
                ],
            },
        )
        planning_routes._planner_pipeline = _pipeline_with_artifacts_and_payload(
            artifact_store=artifact_store,
            issue_payload=issues_payload,
        )

        fake_github = _FakeIssueGitHubClient(token="secret-token")
        monkeypatch.setattr(
            "ace.planning.issue_writer.GitHubAPIClient",
            lambda _token: fake_github,
        )

        session_id = _planning_session_ready(
            client,
            mode="plan_and_create_issues",
        )
        push = _planner_push_envelope(session_id, mode="plan_and_create_issues")
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

        assert len(fake_github.created) == 2
        assert fake_github.created[0][0] == "/repos/owner-one/repo-one/issues"
        assert fake_github.created[1][0] == "/repos/owner-two/repo-two/issues"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        assert event_payload[-2]["event_type"] == "issues_written"
        assert event_payload[-2]["payload"]["requested_count"] == 2
        assert event_payload[-1]["event_type"] == "done"
        assert event_payload[-1]["payload"]["issue_created_count"] == 2

        done_session = client.get(f"/planning/sessions/{session_id}")
        assert done_session.status_code == 200
        assert done_session.json()["status"] == "done"


def test_planner_worker_revises_issues_before_creation(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes
        from ace.config.settings import set_settings_overrides

        set_settings_overrides(
            planning_review_enabled=True,
            planning_review_claude_model="claude-opus-4-6",
            planning_review_openai_model="gpt-5.3",
            planning_review_claude_max_tokens=1800,
            planning_review_openai_max_tokens=3000,
            secrets_backend="env",
            claude_api_key="test-claude-key",
            openai_api_key="test-openai-key",
        )

        artifact_store = _StubArtifactStore()
        planning_routes._artifact_store = artifact_store
        original_issues = json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-001",
                        "repo": "owner-one/repo-one",
                        "title": "Prepare scaffold",
                        "description": "Plan and wire scaffold",
                    },
                ],
            }
        )
        planning_routes._planner_pipeline = _pipeline_with_artifacts_and_payload(
            artifact_store=artifact_store,
            issue_payload=original_issues,
        )

        calls: dict[str, Any] = {}

        async def fake_call_claude(
            prompt: str,
            model: str,
            api_key: str,
            max_tokens: int,
            *,
            trace_name: str = "planning_review_claude",
            metadata: dict | None = None,  # noqa: ARG001
        ) -> str:
            del prompt, api_key, max_tokens, trace_name, metadata
            calls["claude"] = model
            return json.dumps(
                {
                    "plan_recommendations": [
                        "Split the plan into explicit API and frontend tracks."
                    ],
                    "issue_recommendations": [
                        {
                            "issue_id": "ISSUE-001",
                            "recommended_title": "Revised scaffold title",
                            "recommended_description": "Revised description for issue.",
                            "reason": "Aligns with review guidance.",
                        },
                    ],
                    "overall_feedback": "Looks good with minor edits.",
                }
            )

        async def fake_call_openai(
            prompt: str,
            model: str,
            api_key: str,
            max_tokens: int,
            *,
            trace_name: str = "planning_review_openai",
            metadata: dict | None = None,  # noqa: ARG001
        ) -> str:
            del prompt, api_key, max_tokens, trace_name, metadata
            calls["openai"] = model
            return json.dumps(
                {
                    "issues": [
                        {
                            "id": "ISSUE-001",
                            "repo": "owner-one/repo-one",
                            "title": "Revised scaffold title",
                            "description": "Revised description for issue.",
                        },
                    ]
                }
            )

        monkeypatch.setattr(planning_routes, "call_claude", fake_call_claude)
        monkeypatch.setattr(planning_routes, "call_openai", fake_call_openai)

        fake_github = _FakeIssueGitHubClient(token="secret-token")
        monkeypatch.setattr(
            "ace.planning.issue_writer.GitHubAPIClient",
            lambda _token: fake_github,
        )

        session_id = _planning_session_ready(
            client,
            mode="plan_and_create_issues",
        )
        push = _planner_push_envelope(session_id, mode="plan_and_create_issues")
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

        assert fake_github.created
        assert fake_github.created[0][1]["title"] == "Revised scaffold title"
        assert calls["claude"] == "claude-opus-4-6"
        assert calls["openai"] == "gpt-5.3"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_types = [event["event_type"] for event in events.json()["events"]]
        assert "issues_revised" in event_types


def test_planner_worker_review_failures_are_advisory(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes
        from ace.config.settings import set_settings_overrides

        set_settings_overrides(
            planning_review_enabled=True,
            planning_review_claude_model="claude-opus-4-6",
            planning_review_openai_model="gpt-5.3",
            secrets_backend="env",
            claude_api_key="test-claude-key",
            openai_api_key="test-openai-key",
        )

        artifact_store = _StubArtifactStore()
        planning_routes._artifact_store = artifact_store
        original_issues = json.dumps(
            {
                "issues": [
                    {
                        "id": "ISSUE-001",
                        "repo": "owner-one/repo-one",
                        "title": "Prepare scaffold",
                        "description": "Plan and wire scaffold",
                    },
                ],
            }
        )
        planning_routes._planner_pipeline = _pipeline_with_artifacts_and_payload(
            artifact_store=artifact_store,
            issue_payload=original_issues,
        )

        async def fake_call_claude(*_args: Any, **_kwargs: Any) -> str:
            raise RuntimeError("Claude temporarily unavailable")

        async def fail_if_called(*_args: Any, **_kwargs: Any) -> str:
            raise RuntimeError("OpenAI should not be called on review parse failure")

        monkeypatch.setattr(planning_routes, "call_claude", fake_call_claude)
        monkeypatch.setattr(planning_routes, "call_openai", fail_if_called)

        fake_github = _FakeIssueGitHubClient(token="secret-token")
        monkeypatch.setattr(
            "ace.planning.issue_writer.GitHubAPIClient",
            lambda _token: fake_github,
        )

        session_id = _planning_session_ready(
            client,
            mode="plan_and_create_issues",
        )
        push = _planner_push_envelope(session_id, mode="plan_and_create_issues")
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

        assert fake_github.created
        assert fake_github.created[0][1]["title"] == "Prepare scaffold"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        event_types = [event["event_type"] for event in event_payload]
        assert "issues_review_failed" in event_types
        assert "issues_revised" not in event_types


def test_planner_worker_failed_upload_marks_failed_event() -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        failing_store = _FailingArtifactStore()
        planning_routes._artifact_store = failing_store
        planning_routes._planner_pipeline = _pipeline_with_artifacts(failing_store)

        session_id = _planning_session_ready(client)
        push = _planner_push_envelope(session_id)
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 500

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        assert event_payload[-1]["event_type"] == "failed"
        assert event_payload[-1]["payload"]["status"] == "failed"

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "failed"
