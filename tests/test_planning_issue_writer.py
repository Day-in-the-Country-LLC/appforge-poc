"""Tests for planning issue writer behavior."""

from __future__ import annotations

import json

import pytest

from ace.planning.issue_writer import (
    IssueWriterError,
    parse_issues_payload,
    write_issues_from_payload,
)
from ace.planning.models import PlanningMode, PlanningSession


class _FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.posts: list[tuple[str, dict[str, object]]] = []
        del token

    async def rest_post(self, endpoint: str, json: dict[str, object]) -> dict[str, object]:
        self.posts.append((endpoint, json))
        number = len(self.posts)
        return {
            "html_url": f"https://github.com{endpoint.rstrip('/')}/{number}",
            "number": number,
        }

    async def close(self) -> None:
        return None


def test_parse_issues_payload_is_validated() -> None:
    raw = json.dumps(
        {
            "issues": [
                {
                    "id": "I-001",
                    "repo": "owner/alpha",
                    "title": "Initial scaffolding",
                    "description": "Create base project setup",
                    "priority": "high",
                },
            ],
        },
    )
    issues = parse_issues_payload(raw)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.issue_id == "I-001"
    assert issue.repo == "owner/alpha"
    assert issue.priority == "high"


def test_parse_issues_payload_rejects_invalid_json() -> None:
    with pytest.raises(IssueWriterError, match="invalid ISSUES.json payload"):
        parse_issues_payload("{not-valid-json}")


def test_parse_issues_payload_rejects_dependency_cycle() -> None:
    raw = json.dumps(
        {
            "issues": [
                {
                    "id": "I-001",
                    "repo": "owner/one",
                    "title": "First",
                    "depends_on": ["I-002"],
                },
                {
                    "id": "I-002",
                    "repo": "owner/two",
                    "title": "Second",
                    "depends_on": ["I-001"],
                },
            ],
        },
    )
    issues = parse_issues_payload(raw)
    from ace.planning.issue_writer import _dependency_order

    with pytest.raises(IssueWriterError, match="dependency cycle"):
        _dependency_order(issues)


@pytest.mark.asyncio
async def test_write_issues_from_payload_enforces_dependency_order(monkeypatch) -> None:
    fake_client = _FakeGitHubClient(token="token")
    monkeypatch.setattr(
        "ace.planning.issue_writer.GitHubAPIClient",
        lambda token: fake_client,
    )

    raw = json.dumps(
        {
            "issues": [
                {
                    "id": "I-002",
                    "repo": "owner/repo-two",
                    "title": "Second issue",
                    "description": "Depends on first",
                    "depends_on": ["I-001"],
                },
                {
                    "id": "I-001",
                    "repo": "owner/repo-one",
                    "title": "First issue",
                    "description": "No dependencies",
                },
            ],
        },
    )

    session = PlanningSession(
        project_slug="example-project",
        request_text="Enable migration plan",
        mode=PlanningMode.PLAN_ONLY,
    )
    result = await write_issues_from_payload(
        session=session,
        issues_json=raw,
        project_slug=session.project_slug,
        github_token="token",
    )
    assert [issue.issue_id for issue in result] == ["I-001", "I-002"]
    assert len(fake_client.posts) == 2
    assert fake_client.posts[0][0] == "/repos/owner/repo-one/issues"
    assert fake_client.posts[1][0] == "/repos/owner/repo-two/issues"
    assert "Blocked By" in fake_client.posts[1][1]["body"]


@pytest.mark.asyncio
async def test_write_issues_from_payload_reports_dependency_errors() -> None:
    raw = json.dumps(
        {
            "issues": [
                {
                    "id": "I-001",
                    "repo": "owner/one",
                    "title": "Issue A",
                    "depends_on": ["missing"],
                }
            ]
        }
    )
    with pytest.raises(IssueWriterError, match="unknown id"):
        session = PlanningSession(project_slug="example", request_text="Test")
        await write_issues_from_payload(
            session=session,
            issues_json=raw,
            project_slug="example",
            github_token="token",
        )


@pytest.mark.asyncio
async def test_write_issues_from_payload_requires_token() -> None:
    raw = json.dumps({"issues": []})
    with pytest.raises(IssueWriterError, match="GitHub token is required"):
        session = PlanningSession(project_slug="example", request_text="Test")
        await write_issues_from_payload(
            session=session,
            issues_json=raw,
            project_slug="example",
            github_token="",
        )
