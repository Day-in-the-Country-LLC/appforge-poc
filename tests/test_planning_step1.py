"""Step 1 contract tests for planning API stubs."""

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.planning.models import (
    PlanningMode,
    PlanningArtifactType,
    PlanningArtifact,
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


def test_planning_endpoints_are_placeholder_501() -> None:
    with _planning_app_client() as client:
        create_response = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Plan the next release",
            },
        )
        assert create_response.status_code == 501

        message_response = client.post(
            "/planning/sessions/plan-session-01/messages",
            json={
                "source": "user",
                "content": "Primary goal: feature work",
            },
        )
        assert message_response.status_code == 501

        start_response = client.post("/planning/sessions/plan-session-01:start")
        assert start_response.status_code == 501

        get_response = client.get("/planning/sessions/plan-session-01")
        assert get_response.status_code == 501

        events_response = client.get("/planning/sessions/plan-session-01/events")
        assert events_response.status_code == 501

        artifacts_response = client.get("/planning/sessions/plan-session-01/artifacts")
        assert artifacts_response.status_code == 501


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
