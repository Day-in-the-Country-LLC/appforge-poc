"""Planning issue approval endpoint tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ace.config.settings import set_settings_overrides
from ace.webhooks.app import app

_PLANNER_TOKEN = "test-planning-token"
_PLANNER_AUTH_HEADER = {"Authorization": f"Bearer {_PLANNER_TOKEN}"}


def _planning_app_client() -> TestClient:
    set_settings_overrides(
        planner_api_token=_PLANNER_TOKEN,
        webhook_service_role="planner",
        repo_gcp_mapping_path="docs/repo-gcp-mapping.example.json",
        slack_bot_token="",
        slack_channel_id="",
        planning_store_backend="memory",
        github_token="issue-approver-token",
        github_org="example-org",
        github_project_name="ACE Planner",
        github_ready_status="Ready",
    )
    return TestClient(app, headers=_PLANNER_AUTH_HEADER)


class _FakeGitHubAPIClient:
    async def close(self) -> None:
        return None


def test_approve_planning_issues_bulk_move_to_ready(monkeypatch) -> None:
    with _planning_app_client() as client:
        set_calls: list[tuple[int, str, str, str, str, int]] = []

        class _FakeIssueQueue:
            def __init__(self, api_client: object, owner: str, repo: str, projects_client: object) -> None:
                del api_client, projects_client
                self.owner = owner
                self.repo = repo

            async def set_project_status(
                self,
                issue_number: int,
                status: str,
                project_name: str,
                *,
                repo_owner: str,
                repo_name: str,
            ) -> None:
                set_calls.append(
                    (issue_number, status, project_name, repo_owner, repo_name, len(set_calls) + 1)
                )

        monkeypatch.setattr(
            "ace.planning.routes.GitHubAPIClient",
            lambda _token: _FakeGitHubAPIClient(),
        )
        monkeypatch.setattr("ace.planning.routes.IssueQueue", _FakeIssueQueue)
        import ace.planning.routes as planning_routes

        async def fake_request_intake_agent_decision(
            *,
            session: Any,
            messages: Any,
            state: Any,
            settings: Any,
        ) -> Any:
            del session, messages, state, settings
            return planning_routes._IntakeAgentDecision(
                action="ask_user",
                assistant_message="What should this plan optimize for first?",
                repo_question=None,
            )

        monkeypatch.setattr(
            planning_routes,
            "_request_intake_agent_decision",
            fake_request_intake_agent_decision,
        )

        created = client.post(
            "/planning/sessions",
            json={
                "project_slug": "example-project",
                "mode": "plan_only",
                "request_text": "Need approval helper test",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        response = client.post(
            f"/planning/sessions/{session_id}/issues/approve",
            json={
                "issues": [
                    {"issue_id": "ISSUE-1", "repo": "owner-one/repo-one", "number": 12},
                    {"issue_id": "ISSUE-1", "repo": "owner-one/repo-one", "number": 12},
                    {"issue_id": "ISSUE-2", "repo": "owner-two/repo-two", "number": 34},
                    {"issue_id": "BROKEN", "repo": "bad-format", "number": 5},
                    {"issue_id": "ISSUE-3", "repo": "owner-three/repo-three", "number": 0},
                ]
            },
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["requested_count"] == 5
        assert payload["approved_count"] == 2
        assert payload["failed_count"] == 2
        assert payload["approved"][0]["issue_id"] == "ISSUE-1"
        assert payload["approved"][0]["repo"] == "owner-one/repo-one"
        assert payload["approved"][1]["number"] == 34

        # Duplicate issue was accepted once and duplicates are ignored.
        assert len(set_calls) == 2
        assert set_calls[0] == (12, "Ready", "ACE Planner", "owner-one", "repo-one", 1)
        assert set_calls[1] == (34, "Ready", "ACE Planner", "owner-two", "repo-two", 2)
