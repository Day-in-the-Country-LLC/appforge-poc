"""Planner worker endpoint tests."""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.webhooks.app import app


class _StubArtifactStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_plan_markdown(self, session_id: str, content: str) -> str:
        self.writes.append((session_id, content))
        return f"https://example.test/{session_id}/PLAN.md"


class _FailingArtifactStore:
    async def write_plan_markdown(self, session_id: str, content: str) -> str:  # noqa: ARG002
        del session_id, content
        raise RuntimeError("artifact upload unavailable")


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

        session_id = _planning_session_ready(client)
        push = _planner_push_envelope(session_id)
        resp = client.post("/internal/pubsub/planner", json=push)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        assert resp.json()["session_id"] == session_id

        assert artifact_store.writes == [(session_id, "stub")]

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
        assert len(artifact_rows) == 1
        assert artifact_rows[0]["artifact_type"] == "plan_md"
        assert artifact_rows[0]["content_url"] == "https://example.test/{}/PLAN.md".format(
            session_id,
        )


def test_planner_worker_failed_upload_marks_failed_event() -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        planning_routes._artifact_store = _FailingArtifactStore()

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
