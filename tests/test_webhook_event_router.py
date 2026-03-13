from ace.webhooks.event_router import WorkEventRouter


def test_router_routes_supported_github_events() -> None:
    router = WorkEventRouter()
    event = router.route(
        source="github",
        event="pull_request",
        payload={
            "action": "opened",
            "number": 42,
            "pull_request": {"number": 42},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
        delivery="delivery-1",
    )
    assert event.is_supported
    assert not event.is_ignored
    assert event.source == "github"
    assert event.event_type == "pull_request"
    assert event.action == "opened"
    assert event.repository_owner == "Acme-Corp"
    assert event.repository_name == "widget-api"
    assert event.issue_number == 42


def test_router_marks_unsupported_events_for_non_github() -> None:
    router = WorkEventRouter()
    event = router.route(
        source="linear",
        event="issue_comment",
        payload={
            "issue": {"number": 11},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
    )
    assert not event.is_supported
    assert event.is_ignored
    assert event.event_type == "unsupported"


def test_router_routes_issue_comment_payload() -> None:
    router = WorkEventRouter()
    event = router.route(
        source="github",
        event="issue_comment",
        payload={
            "action": "created",
            "issue": {"number": 14, "pull_request": {"id": "42"}},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
        delivery="delivery-3",
    )
    assert event.is_supported
    assert event.event_type == "issue_comment"
    assert event.is_pull_request_comment
    assert event.issue_number == 14


def test_router_routes_issues_payload() -> None:
    router = WorkEventRouter()
    event = router.route(
        source="github",
        event="issues",
        payload={
            "action": "closed",
            "issue": {"number": 99},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
        delivery="delivery-4",
    )
    assert event.is_supported
    assert event.event_type == "issues"
    assert event.issue_number == 99


def test_router_routes_projects_v2_item_payload() -> None:
    router = WorkEventRouter()
    event = router.route(
        source="github",
        event="projects_v2_item",
        payload={
            "action": "edited",
            "projects_v2_item": {
                "node_id": "item-123",
                "project_node_id": "project-9",
                "content_node_id": "content-77",
                "project": {"title": "Acme Platform"},
                "content": {"number": 77},
                "repository": {
                    "name": "widget-api",
                    "owner": {"login": "Acme-Corp"},
                },
            },
            "changes": {
                "field_value": {
                    "field_name": "status",
                    "from": "Backlog",
                    "to": "Ready",
                }
            },
            "project": {"title": "Acme Platform"},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
        delivery="delivery-5",
    )
    assert event.is_supported
    assert event.event_type == "projects_v2_item"
    assert event.item_node_id == "item-123"
    assert event.project_node_id == "project-9"
    assert event.content_node_id == "content-77"
