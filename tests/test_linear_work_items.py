"""Tests for Linear-backed work-item tracker behavior."""

from __future__ import annotations

import pytest

from ace.linear import work_items


class _StubLinearClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def graphql(self, query: str, variables: dict[str, object] | None = None):
        self.calls.append((query, variables or {}))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_list_issues_by_project_status_filters_by_state() -> None:
    client = _StubLinearClient(
        [
            {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "number": 7,
                                "title": "Ready item",
                                "url": "https://github.com/org/demo/issues/7",
                                "state": {"name": "Ready"},
                                "labels": {"nodes": [{"name": "task"}]},
                                "repository": {
                                    "owner": {"login": "org"},
                                    "name": "demo",
                                },
                            },
                            {
                                "id": "issue-2",
                                "number": 8,
                                "title": "Backlog item",
                                "url": "https://github.com/org/demo/issues/8",
                                "state": {"name": "Backlog"},
                                "labels": {"nodes": [{"name": "task"}]},
                                "repository": {
                                    "owner": {"login": "org"},
                                    "name": "demo",
                                },
                            },
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        ]
    )

    async def _fake_get_project_id(_project_name: str) -> str:
        return "team-1"

    tracker = work_items.LinearWorkItemTracker(client)
    tracker.get_project_id = _fake_get_project_id  # type: ignore[method-assign]

    issues = await tracker.list_issues_by_project_status("Team One", "ready")

    assert len(issues) == 1
    assert issues[0].number == 7
    assert issues[0].title == "Ready item"
    assert issues[0].labels == ["task"]


@pytest.mark.asyncio
async def test_get_issue_blockers_reads_linear_dependency_nodes() -> None:
    ref = work_items._LinearIssueRef(
        id="issue-1",
        number=42,
        title="Current",
        repo_owner="org",
        repo_name="demo",
        status="In Progress",
        html_url="https://github.com/org/demo/issues/42",
    )
    blocker_payload = {
        "issue": {
            "blockedByIssues": {
                "nodes": [
                    {
                        "number": 10,
                        "title": "Blocking One",
                        "url": "https://github.com/org/demo/issues/10",
                        "state": {"name": "OPEN"},
                    },
                    {
                        "number": "11",
                        "title": "Blocking Two",
                        "url": "https://github.com/org/demo/issues/11",
                        "state": {"name": "DONE"},
                    },
                ]
            },
            "dependencies": {
                "nodes": [
                    {
                        "number": 12,
                        "title": "Dependency",
                        "url": "https://github.com/org/demo/issues/12",
                        "state": {"name": "In Progress"},
                    }
                ]
            },
        }
    }

    client = _StubLinearClient([{"issue": blocker_payload["issue"]}])
    async def _fake_find_issue(
        issue_number: int,
        repo_owner: str,
        repo_name: str,
        project_name: str | None = None,
        project_id: str | None = None,
    ):
        return ref

    tracker = work_items.LinearWorkItemTracker(client)
    tracker._find_issue = _fake_find_issue  # type: ignore[method-assign]

    blockers = await tracker.get_issue_blockers("org", "demo", 42)

    assert [b.number for b in blockers] == [10, 11, 12]
    assert [b.state for b in blockers] == ["OPEN", "DONE", "In Progress"]
    assert [b.title for b in blockers] == ["Blocking One", "Blocking Two", "Dependency"]
