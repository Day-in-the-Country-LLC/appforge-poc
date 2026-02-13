import pytest

from ace.webhooks.lifecycle import (
    RESOLUTION_BLOCKED,
    RESOLUTION_FAILURE,
    RESOLUTION_PR_OPENED,
    RESOLUTION_SUCCESS,
    RESOLUTION_TIMEOUT,
    build_lifecycle_context,
    normalize_error_resolution,
    normalize_result_resolution,
)


def test_build_lifecycle_context_extracts_issue_and_project():
    context = build_lifecycle_context(
        event="issue_comment",
        payload={
            "action": "created",
            "project": {"title": "Appforge"},
            "issue": {"number": 12},
            "repository": {"name": "digido", "owner": {"login": "Day-in-the-Country-LLC"}},
        },
        delivery="delivery-123",
        repo_gcp_mapping={"day-in-the-country-llc/digido": "digido-assistant"},
    )
    assert context.event == "issue_comment"
    assert context.action == "created"
    assert context.project == "Appforge"
    assert context.issue_key == "Day-in-the-Country-LLC/digido#12"
    assert context.target_gcp_project == "digido-assistant"
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
        project="Appforge",
        issue_key="Day-in-the-Country-LLC/digido#34",
        target_gcp_project="digido-assistant",
        action="edited",
    )
    assert context.project == "Appforge"
    assert context.issue_key == "Day-in-the-Country-LLC/digido#34"
    assert context.target_gcp_project == "digido-assistant"
    assert context.action == "edited"


def test_build_lifecycle_context_fails_for_unmapped_repo():
    with pytest.raises(ValueError, match="❌ ERROR: repo_gcp_project_mapping_missing"):
        build_lifecycle_context(
            event="issue_comment",
            payload={
                "issue": {"number": 12},
                "repository": {
                    "name": "missing-repo",
                    "owner": {"login": "Day-in-the-Country-LLC"},
                },
            },
            delivery="delivery-123",
            repo_gcp_mapping={"day-in-the-country-llc/digido": "digido-assistant"},
        )


def test_normalize_result_resolution():
    assert normalize_result_resolution({"status": "blocked"}) == RESOLUTION_BLOCKED
    assert normalize_result_resolution({"status": "failed"}) == RESOLUTION_FAILURE
    assert normalize_result_resolution({"status": "task_wait_timeout"}) == RESOLUTION_TIMEOUT
    assert normalize_result_resolution({"action": "pr_opened"}) == RESOLUTION_PR_OPENED
    assert normalize_result_resolution({"status": "triggered"}) == RESOLUTION_SUCCESS


def test_normalize_error_resolution():
    assert normalize_error_resolution(RuntimeError("task_wait_timeout")) == RESOLUTION_TIMEOUT
    assert normalize_error_resolution(RuntimeError("boom")) == RESOLUTION_FAILURE
