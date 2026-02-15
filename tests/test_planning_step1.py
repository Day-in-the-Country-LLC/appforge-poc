"""Step 3 planning API tests."""

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.planning.models import (
    PlanningArtifactType,
    PlanningArtifact,
    PlanningMode,
    PlanningSession,
)
from ace.webhooks.app import app


def _planning_app_client() -> TestClient:
    set_settings_overrides(
        webhook_service_role="planner",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
    )
    return TestClient(app)


def test_planning_session_intake_and_state_machine() -> None:
    with _planning_app_client() as client:
        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Plan the next release",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        session_id = payload["id"]
        assert payload["status"] == "intake_pending"
        assert len(payload["questions"]) == 3
        assert payload["status"] == "intake_pending"
        assert payload["questions"][0]["id"] == "primary_goal_category"

        bad_message = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"question_id": "bad", "answer": "Feature", "source": "user"},
        )
        assert bad_message.status_code == 400

        start_before = client.post(f"/planning/sessions/{session_id}:start")
        assert start_before.status_code == 409

        response = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][0]["id"],
                "answer": "feature",
                "source": "user",
            },
        )
        assert response.status_code == 200
        response = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][1]["id"],
                "answer": "Ship to staging",
                "source": "user",
            },
        )
        assert response.status_code == 200
        response = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][2]["id"],
                "answer": "example-project/appforge-poc",
                "source": "user",
            },
        )
        assert response.status_code == 200

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "ready_to_run"

        started = client.post(f"/planning/sessions/{session_id}:start")
        assert started.status_code == 200
        assert started.json()["status"] == "running"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()
        assert event_payload["events"][0]["event_type"] == "session_created"
        assert event_payload["events"][-1]["event_type"] == "session_started"

        artifacts = client.get(f"/planning/sessions/{session_id}/artifacts")
        assert artifacts.status_code == 200
        assert artifacts.json()["artifacts"] == []


def test_planning_models_and_enums() -> None:
    artifact = PlanningArtifact(
        session_id="plan-session-01",
        artifact_type=PlanningArtifactType.PLAN_MARKDOWN,
        content_url="https://example.com/artifacts/plan.md",
    )
    assert artifact.session_id == "plan-session-01"
    assert artifact.artifact_type == PlanningArtifactType.PLAN_MARKDOWN

    session = PlanningSession(
        project_slug="example-project",
        request_text="Plan checkout rollout.",
        mode=PlanningMode.PLAN_ONLY,
    )
    assert session.mode == PlanningMode.PLAN_ONLY
