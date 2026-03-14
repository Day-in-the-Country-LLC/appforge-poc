import pytest

from ace.webhooks.lifecycle import (
    RESOLUTION_BLOCKED,
    RESOLUTION_FAILURE,
    RESOLUTION_PR_OPENED,
    RESOLUTION_SUCCESS,
    RESOLUTION_TIMEOUT,
    build_lifecycle_context,
    build_session_lifecycle_context,
    log_session_lifecycle_event,
    STAGE_SESSION_START,
    normalize_error_resolution,
    normalize_result_resolution,
)


def test_build_lifecycle_context_extracts_issue_and_project():
    context = build_lifecycle_context(
        event="issue_comment",
        payload={
            "action": "created",
            "project": {"title": "Acme Platform"},
            "issue": {"number": 12},
            "repository": {"name": "widget-api", "owner": {"login": "Acme-Corp"}},
        },
        delivery="delivery-123",
        repo_gcp_mapping={"acme-corp/widget-api": "widget-prod-123456"},
    )
    assert context.event == "issue_comment"
    assert context.action == "created"
    assert context.project == "Acme Platform"
    assert context.issue_key == "Acme-Corp/widget-api#12"
    assert context.target_gcp_project == "widget-prod-123456"
    assert context.delivery_id == "delivery-123"
    assert context.workflow_id == "delivery-123"


def test_build_lifecycle_context_uses_default_project_and_generated_workflow_id():
    context = build_lifecycle_context(
        event="projects_v2_item",
        payload={},
        delivery=None,
        default_project="Default Project",
    )
    assert context.project == "Default Project"
    assert context.target_gcp_project is None
    assert context.workflow_id.startswith("wf-")


def test_build_lifecycle_context_uses_explicit_correlation_overrides():
    context = build_lifecycle_context(
        event="projects_v2_item",
        payload={},
        delivery="delivery-1",
        project="Acme Platform",
        issue_key="Acme-Corp/widget-api#34",
        target_gcp_project="widget-prod-123456",
        action="edited",
    )
    assert context.project == "Acme Platform"
    assert context.issue_key == "Acme-Corp/widget-api#34"
    assert context.target_gcp_project == "widget-prod-123456"
    assert context.action == "edited"


def test_build_lifecycle_context_normalizes_source():
    context = build_lifecycle_context(
        event="issue_comment",
        payload={},
        delivery="delivery-1",
        source="LINEAR",
    )
    assert context.source == "linear"


def test_build_lifecycle_context_fails_for_unmapped_repo():
    with pytest.raises(ValueError, match="❌ ERROR: repo_gcp_project_mapping_missing"):
        build_lifecycle_context(
            event="issue_comment",
            payload={
                "issue": {"number": 12},
                "repository": {
                    "name": "missing-repo",
                    "owner": {"login": "Acme-Corp"},
                },
            },
            delivery="delivery-123",
            repo_gcp_mapping={"acme-corp/widget-api": "widget-prod-123456"},
        )


def test_build_session_lifecycle_context_requires_turn_number_and_workflow():
    with pytest.raises(ValueError, match="turn_number"):
        build_session_lifecycle_context(
            session_id="run-123",
            turn_number=0,
            workflow_id="wf-1",
            source="github",
            issue_key="acme/widget#123",
        )
    with pytest.raises(ValueError, match="workflow_id"):
        build_session_lifecycle_context(
            session_id="run-123",
            turn_number=1,
            workflow_id="",
            source="github",
            issue_key="acme/widget#123",
        )


def test_build_session_lifecycle_context_normalizes_source_and_emits_fields():
    context = build_session_lifecycle_context(
        session_id="run-123",
        turn_number=1,
        workflow_id="wf-1",
        source="LINEAR",
        issue_key="acme/widget#123",
    )
    assert context.source == "linear"

    events: list[dict[str, object]] = []

    class _BoundLogger:
        def __init__(self) -> None:
            self._events = events
            self._bound = {}

        def bind(self, **kwargs):
            child = _BoundLogger()
            child._events = self._events
            child._bound = {**self._bound, **kwargs}
            return child

        def info(self, event_name: str, **fields: object) -> None:
            merged = {**self._bound}
            merged.update(fields)
            self._events.append({"event_name": event_name, "fields": merged})

    logger = _BoundLogger()
    log_session_lifecycle_event(logger, STAGE_SESSION_START, context)
    assert events and events[0]["event_name"] == "session_lifecycle"
    fields = events[0]["fields"]
    assert fields["source"] == "linear"
    assert fields["session_id"] == "run-123"
    assert fields["turn_number"] == 1
    assert fields["issue_key"] == "acme/widget#123"
    assert fields["stage"] == STAGE_SESSION_START


def test_normalize_result_resolution():
    assert normalize_result_resolution({"status": "blocked"}) == RESOLUTION_BLOCKED
    assert normalize_result_resolution({"status": "failed"}) == RESOLUTION_FAILURE
    assert normalize_result_resolution({"status": "task_wait_timeout"}) == RESOLUTION_TIMEOUT
    assert normalize_result_resolution({"action": "pr_opened"}) == RESOLUTION_PR_OPENED
    assert normalize_result_resolution({"status": "triggered"}) == RESOLUTION_SUCCESS


def test_normalize_error_resolution():
    assert normalize_error_resolution(RuntimeError("task_wait_timeout")) == RESOLUTION_TIMEOUT
    assert normalize_error_resolution(RuntimeError("boom")) == RESOLUTION_FAILURE
