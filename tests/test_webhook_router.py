"""Tests for webhook event normalization."""

from ace.webhooks.event_router import WorkEventRouter


def test_route_github_projects_v2_item_normalizes_status() -> None:
    payload = {
        "projects_v2_item": {
            "id": "item-101",
            "content": {"id": "CONTENT-1"},
        },
        "project": {"project_node_id": "project-1", "title": "Appforge"},
        "changes": {
            "field_value": {
                "field_name": "status",
                "from": {"name": "Backlog"},
                "to": {"name": "Ready"},
            }
        },
        "action": "edited",
    }

    routed = WorkEventRouter.route(
        "projects_v2_item",
        payload,
        default_project="Default Project",
    )

    assert routed is not None
    assert routed.source == "github"
    assert routed.event == "projects_v2_item"
    assert routed.project_id == "project-1"
    assert routed.project_name == "Appforge"
    assert routed.transition_from == "Backlog"
    assert routed.transition_to == "Ready"
    assert routed.is_github_ready_transition


def test_route_github_issue_comment_parses_pull_request_comment() -> None:
    payload = {
        "action": "created",
        "issue": {"number": 99, "pull_request": {"url": "https://example.com/pr"}},
        "repository": {"name": "app", "owner": {"login": "acme"}},
    }

    routed = WorkEventRouter.route("issue_comment", payload)

    assert routed is not None
    assert routed.source == "github"
    assert routed.event == "issue_comment"
    assert routed.action == "created"
    assert routed.issue_number == 99
    assert routed.repo_owner == "acme"
    assert routed.repo_name == "app"
    assert routed.is_pull_request


def test_route_linear_webhook_detects_issue_transition() -> None:
    payload = {
        "type": "linear_issue",
        "issue": {
            "number": 12,
            "state": {"name": "In Progress"},
            "repository": {"name": "backend", "organization": {"key": "acme"}},
        },
        "previousValues": {"state": {"name": "Ready"}},
        "team": {"id": "team-77", "name": "Core Team"},
    }

    routed = WorkEventRouter.route("linear_issue", payload)

    assert routed is not None
    assert routed.source == "linear"
    assert routed.event == "linear_issue"
    assert routed.issue_number == 12
    assert routed.repo_owner == "acme"
    assert routed.repo_name == "backend"
    assert routed.project_id == "team-77"
    assert routed.project_name == "Core Team"
    assert routed.transition_from == "Ready"
    assert routed.transition_to == "In Progress"
