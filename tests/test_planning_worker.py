"""Planner worker endpoint tests."""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.planning.models import PlanningArtifactType
from ace.webhooks.app import app


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


def _prepare_stub_pipeline(
    planning_routes: Any,
    artifact_store: _StubArtifactStore,
) -> None:
    planning_routes._planner_pipeline = _pipeline_with_artifacts(artifact_store)


def _planning_app_client() -> TestClient:
    set_settings_overrides(
        webhook_service_role="all",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
    )
    return TestClient(app)


def _planning_session_ready(client: TestClient, project_slug: str = "example-project") -> str:
    created = client.post(
        "/planning/sessions",
        json={
            "project_slug": project_slug,
            "mode": "plan_only",
            "request_text": "Build a migration plan",
        },
    )
    assert created.status_code == 201
    payload = created.json()
    session_id = payload["id"]

    for question in payload["questions"]:
        created_answer = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": question["id"],
                "answer": "feature",
                "source": "user",
            },
        )
        assert created_answer.status_code == 200

    session = client.get(f"/planning/sessions/{session_id}")
    assert session.status_code == 200
    assert session.json()["status"] == "ready_to_run"

    return session_id


def _planner_push_envelope(session_id: str) -> dict[str, object]:
    raw = {
        "schema_version": "1",
        "event": "planner.start",
        "payload": {
            "session_id": session_id,
            "project_slug": "example-project",
            "mode": "plan_only",
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
                "mode": "plan_only",
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
