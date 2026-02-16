"""Planning API route tests."""

from typing import Any

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
        planning_intake_agent_enabled=True,
    )
    return TestClient(app, headers=_PLANNER_AUTH_HEADER)


def _stub_intake_agent_decisions(monkeypatch: Any, decisions: list[dict[str, Any]]) -> None:
    import ace.planning.routes as planning_routes

    decision_iter = iter(decisions)

    async def fake_request_intake_agent_decision(
        *,
        session: Any,
        messages: Any,
        state: Any,
        settings: Any,
    ) -> Any:
        del session, messages, state, settings
        raw = next(decision_iter)
        return planning_routes._IntakeAgentDecision(
            action=raw["action"],
            assistant_message=raw.get("assistant_message", ""),
            repo_question=raw.get("repo_question"),
        )

    monkeypatch.setattr(
        planning_routes,
        "_request_intake_agent_decision",
        fake_request_intake_agent_decision,
    )


def test_planning_session_intake_and_state_machine(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        _stub_intake_agent_decisions(
            monkeypatch,
            [
                {
                    "action": "ready_to_plan",
                    "assistant_message": "Intake complete. Click Start planning when ready.",
                }
            ],
        )

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

        response = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={
                "content": "feature plan for staging rollout",
                "source": "user",
            },
        )
        assert response.status_code == 200

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "running"

        assert len(stub_queue.published) == 1
        assert stub_queue.published[0]["session_id"] == session_id
        assert stub_queue.published[0]["project_slug"] == "example-project"
        assert stub_queue.published[0]["mode"] == "plan_only"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()
        assert event_payload["events"][0]["event_type"] == "session_created"
        event_types = [event["event_type"] for event in event_payload["events"]]
        assert "queued" in event_types
        assert "running" in event_types
        assert event_types.index("queued") < event_types.index("running")

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
        planning_intake_agent_enabled=True,
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

        _stub_intake_agent_decisions(
            monkeypatch,
            [
                {
                    "action": "ready_to_plan",
                    "assistant_message": "Intake complete. Click Start planning when ready.",
                }
            ],
        )

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

        assert (
            client.post(
                f"/planning/sessions/{session_id}/messages",
                json={"content": "Feature planning with smoke test success criteria"},
            ).status_code
            == 500
        )

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_payload = events.json()["events"]
        assert event_payload[-1]["event_type"] == "failed"


def test_listener_role_serves_planning_but_not_worker_endpoint() -> None:
    set_settings_overrides(
        planner_api_token=_PLANNER_TOKEN,
        webhook_service_role="listener",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
        planning_intake_agent_enabled=True,
    )

    with TestClient(app, headers=_PLANNER_AUTH_HEADER) as client:
        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Listener role planning access",
            },
        )
        assert created.status_code == 201

        worker = client.post("/internal/pubsub/worker", json={})
        assert worker.status_code == 404
        assert worker.json()["detail"] == "❌ ERROR: worker endpoint disabled"


def test_conversational_intake_flow_and_messages_endpoint(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        planning_routes._planner_queue = _StubPlannerQueue()
        _stub_intake_agent_decisions(
            monkeypatch,
            [
                {
                    "action": "ask_user",
                    "assistant_message": "What are your success criteria?",
                },
                {
                    "action": "ask_user",
                    "assistant_message": "Which repos should be in scope?",
                },
                {
                    "action": "ready_to_plan",
                    "assistant_message": "Intake complete. Click Start planning when ready.",
                },
            ],
        )

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Plan the next release using a conversation",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        messages = client.get(f"/planning/sessions/{session_id}/messages")
        assert messages.status_code == 200
        history = messages.json()["messages"]
        assert history
        assert history[0]["source"] == "assistant"

        first = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "Feature", "source": "user"},
        )
        assert first.status_code == 200
        assert first.json()["source"] == "assistant"

        second = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "Ship to staging", "source": "user"},
        )
        assert second.status_code == 200
        assert second.json()["source"] == "assistant"

        third = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "example-project/appforge-poc", "source": "user"},
        )
        assert third.status_code == 200
        assert third.json()["source"] == "assistant"
        assert "Intake complete" in third.json()["content"]

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["status"] == "running"


def test_agent_intake_can_query_repo_agents_and_finish(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes
        from ace.planning.models import PlanningProjectRegistry, PlanningProjectRepository
        from ace.planning.scouts import ScoutReport

        planning_routes._planner_queue = _StubPlannerQueue()

        decisions = iter(
            [
                {
                    "action": "ask_repo_agents",
                    "assistant_message": "",
                    "repo_question": "Which repos and entrypoints are relevant?",
                },
                {
                    "action": "ask_user",
                    "assistant_message": "What does success look like for this plan?",
                },
                {
                    "action": "ready_to_plan",
                    "assistant_message": "Intake complete. Click Start planning when ready.",
                },
            ]
        )

        async def fake_request_intake_agent_decision(
            *,
            session: Any,
            messages: Any,
            state: Any,
            settings: Any,
        ) -> Any:
            del session, messages, state, settings
            raw = next(decisions)
            return planning_routes._IntakeAgentDecision(
                action=raw["action"],
                assistant_message=raw.get("assistant_message", ""),
                repo_question=raw.get("repo_question"),
            )

        async def fake_load_project_registry(
            project_slug: str,
            *,
            settings: Any | None = None,
        ) -> PlanningProjectRegistry:
            del settings
            return PlanningProjectRegistry(
                project_slug=project_slug,
                repos=[
                    PlanningProjectRepository(
                        owner="example-project",
                        name="appforge-poc",
                        local_path="/tmp/example-project/appforge-poc",
                    )
                ],
            )

        async def fake_run_repositories_scout(
            repos: Any,
            *,
            github_token: str,
        ) -> list[ScoutReport]:
            del repos, github_token
            return [
                ScoutReport(
                    repo="example-project/appforge-poc",
                    summary="Scanned planner entrypoints and config.",
                    entrypoints=["scripts/ace_dashboard.py"],
                    risks=["Missing acceptance tests for planner flow."],
                    work_items=["Add tests for intake orchestration and review handoff."],
                )
            ]

        monkeypatch.setattr(
            planning_routes,
            "_request_intake_agent_decision",
            fake_request_intake_agent_decision,
        )
        monkeypatch.setattr(planning_routes, "load_project_registry", fake_load_project_registry)
        monkeypatch.setattr(planning_routes, "run_repositories_scout", fake_run_repositories_scout)

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Plan intake orchestration updates",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        first_turn = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "Need a plan for planner intake improvements.", "source": "user"},
        )
        assert first_turn.status_code == 200
        assert first_turn.json()["source"] == "assistant"
        assert "success look like" in first_turn.json()["content"]

        second_turn = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "Ship to staging with CI green", "source": "user"},
        )
        assert second_turn.status_code == 200
        assert "Intake complete" in second_turn.json()["content"]

        session = client.get(f"/planning/sessions/{session_id}")
        assert session.status_code == 200
        payload = session.json()
        assert payload["status"] == "running"

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_types = [event["event_type"] for event in events.json()["events"]]
        assert "intake_repo_agents_answered" in event_types


def test_agent_intake_limits_repo_scouts_per_user_turn(monkeypatch) -> None:
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes
        from ace.planning.models import PlanningProjectRegistry, PlanningProjectRepository
        from ace.planning.scouts import ScoutReport

        set_settings_overrides(planning_intake_max_repo_scouts_per_turn=2)

        decisions = iter(
            [
                {
                    "action": "ask_repo_agents",
                    "assistant_message": "",
                    "repo_question": "First scout question",
                },
                {
                    "action": "ask_repo_agents",
                    "assistant_message": "",
                    "repo_question": "Second scout question",
                },
                {
                    "action": "ask_repo_agents",
                    "assistant_message": "",
                    "repo_question": "Third scout question should fail",
                },
            ]
        )

        async def fake_request_intake_agent_decision(
            *,
            session: Any,
            messages: Any,
            state: Any,
            settings: Any,
        ) -> Any:
            del session, messages, state, settings
            raw = next(decisions)
            return planning_routes._IntakeAgentDecision(
                action=raw["action"],
                assistant_message=raw.get("assistant_message", ""),
                repo_question=raw.get("repo_question"),
            )

        async def fake_load_project_registry(
            project_slug: str,
            *,
            settings: Any | None = None,
        ) -> PlanningProjectRegistry:
            del settings
            return PlanningProjectRegistry(
                project_slug=project_slug,
                repos=[
                    PlanningProjectRepository(
                        owner="example-project",
                        name="appforge-poc",
                        local_path="/tmp/example-project/appforge-poc",
                    )
                ],
            )

        async def fake_run_repositories_scout(
            repos: Any,
            *,
            github_token: str,
        ) -> list[ScoutReport]:
            del repos, github_token
            return [
                ScoutReport(
                    repo="example-project/appforge-poc",
                    summary="Scanned repo for intake support.",
                    entrypoints=["scripts/ace_dashboard.py"],
                    risks=[],
                    work_items=["Use intake signals in planning."],
                )
            ]

        monkeypatch.setattr(
            planning_routes,
            "_request_intake_agent_decision",
            fake_request_intake_agent_decision,
        )
        monkeypatch.setattr(planning_routes, "load_project_registry", fake_load_project_registry)
        monkeypatch.setattr(planning_routes, "run_repositories_scout", fake_run_repositories_scout)

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Plan with strict repo scout limits",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        response = client.post(
            f"/planning/sessions/{session_id}/messages",
            json={"content": "Please gather all code context before planning.", "source": "user"},
        )
        assert response.status_code == 500
        assert "exceeded max repo scouts for this user turn (2)" in response.json()["detail"]


def test_user_cannot_start_planning_while_intake_pending() -> None:
    """Start must be blocked until intake agent marks the session ready."""
    with _planning_app_client() as client:
        import ace.planning.routes as planning_routes

        stub_queue = _StubPlannerQueue()
        planning_routes._planner_queue = stub_queue

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Quick plan without intake conversation",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]
        assert created.json()["status"] == "intake_pending"

        started = client.post(f"/planning/sessions/{session_id}:start")
        assert started.status_code == 409
        assert started.json()["detail"] == "❌ ERROR: session cannot start (status=intake_pending)"

        assert len(stub_queue.published) == 0

        events = client.get(f"/planning/sessions/{session_id}/events")
        assert events.status_code == 200
        event_list = events.json()["events"]
        event_types = [event["event_type"] for event in event_list]
        assert "intake_complete" not in event_types
