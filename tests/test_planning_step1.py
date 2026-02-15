"""Step 3 planning API tests."""

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.planning.models import (
    PlanningArtifact,
    PlanningArtifactType,
    PlanningMode,
    PlanningSession,
)
from ace.webhooks.app import app

_PLANNER_TOKEN = "test-planning-token"
_PLANNER_AUTH_HEADER = {"Authorization": f"Bearer {_PLANNER_TOKEN}"}

class _StubPlannerQueue:
    def __init__(self) -> None:
        self.published: list[dict[str, str]] = []

    async def publish(
        self,
        *,
        session_id: str,
        project_slug: str,
        mode: str,
        created_at: str,
    ) -> str:
        self.published.append(
            {
                "session_id": session_id,
                "project_slug": project_slug,
                "mode": mode,
                "created_at": created_at,
            }
        )
        return "message-id-123"


class _FailingPlannerQueue:
    async def publish(
        self,
        *,
        session_id: str,
        project_slug: str,
        mode: str,
        created_at: str,
    ) -> str:
        del session_id, project_slug, mode, created_at
        raise RuntimeError("queue unavailable")


def _planning_app_client() -> TestClient:
    set_settings_overrides(
        planner_api_token=_PLANNER_TOKEN,
        webhook_service_role="planner",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
    )
    return TestClient(app, headers=_PLANNER_AUTH_HEADER)


def test_planning_session_intake_and_state_machine(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        stub_queue = _StubPlannerQueue()
        planning_routes._planner_queue = stub_queue

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

        assert len(stub_queue.published) == 1
        assert stub_queue.published[0]["session_id"] == session_id
        assert stub_queue.published[0]["project_slug"] == "example-project"
        assert stub_queue.published[0]["mode"] == "plan_only"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()
        assert event_payload["events"][0]["event_type"] == "session_created"
        assert event_payload["events"][-2:][0]["event_type"] == "queued"
        assert event_payload["events"][-1]["event_type"] == "running"

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


def test_planning_endpoints_require_bearer_token() -> None:
    set_settings_overrides(
        planner_api_token=_PLANNER_TOKEN,
        webhook_service_role="planner",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
    )
    endpoint = "/planning/sessions"
    payload = {
        "project_slug": "example-project",
        "mode": "plan_only",
        "request_text": "Plan auth checks",
    }

    with TestClient(app) as client:
        unauth = client.post(endpoint, json=payload)
        assert unauth.status_code == 401
        assert "Authorization" in unauth.json()["detail"]

    with TestClient(app, headers={"Authorization": "Bearer wrong-token"}) as client:
        wrong = client.post(endpoint, json=payload)
        assert wrong.status_code == 401
        assert wrong.json()["detail"].startswith("❌ ERROR: invalid")

    with TestClient(app, headers=_PLANNER_AUTH_HEADER) as client:
        auth = client.post(endpoint, json=payload)
        assert auth.status_code == 201


def test_start_planning_failure_emits_failed_event(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        planning_routes._planner_queue = _FailingPlannerQueue()

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Handle the start failure path",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        session_id = payload["id"]

        assert client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][0]["id"],
                "answer": "feature",
            },
        ).status_code == 200
        assert client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][1]["id"],
                "answer": "Smoke test",
            },
        ).status_code == 200
        assert client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "question_id": payload["questions"][2]["id"],
                "answer": "example-project/appforge-poc",
            },
        ).status_code == 200

        started = client.post(f"/planning/sessions/{session_id}:start")
        assert started.status_code == 500

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        assert event_payload[-1]["event_type"] == "failed"
