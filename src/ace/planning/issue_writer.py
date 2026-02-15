"""Issue creation stage for planning sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ace.github.api_client import GitHubAPIClient
from ace.planning.models import PlanningSession


class IssueWriterError(ValueError):
    """Domain error for issue writer failures."""


@dataclass(frozen=True)
class PlannedIssue:
    """One planned issue item extracted from planning artifacts."""

    issue_id: str
    repo: str
    title: str
    description: str
    priority: str | None = None
    depends_on: list[str] | None = None


@dataclass(frozen=True)
class CreatedIssue:
    """Created issue details from the GitHub API."""

    issue_id: str
    repo: str
    title: str
    url: str
    number: int
    blockers: list[str]


def parse_issues_payload(raw: str) -> list[PlannedIssue]:
    """Parse planning `ISSUES.json` into normalized issue items."""
    try:
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - exercised via test
        raise IssueWriterError(f"❌ ERROR: invalid ISSUES.json payload: {exc}") from exc

    issues_payload = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues_payload, list):
        raise IssueWriterError("❌ ERROR: ISSUES.json payload must contain an issues array")

    issues: list[PlannedIssue] = []
    seen: set[str] = set()
    for index, raw_issue in enumerate(issues_payload):
        if not isinstance(raw_issue, dict):
            raise IssueWriterError(
                f"❌ ERROR: invalid issue entry at index {index}; expected object"
            )

        issue_id = str(raw_issue.get("id") or "").strip()
        if not issue_id:
            raise IssueWriterError(f"❌ ERROR: missing issue id at index {index}")
        if issue_id in seen:
            raise IssueWriterError(f"❌ ERROR: duplicate issue id '{issue_id}'")
        seen.add(issue_id)

        repo = str(raw_issue.get("repo") or "").strip()
        if "/" not in repo:
            raise IssueWriterError(
                f"❌ ERROR: issue '{issue_id}' has invalid repo '{repo}', expected owner/repo"
            )

        title = str(raw_issue.get("title") or "").strip()
        if not title:
            raise IssueWriterError(f"❌ ERROR: issue '{issue_id}' missing title")

        raw_depends_on = raw_issue.get("depends_on", [])
        if raw_depends_on in (None, ""):
            depends_on: list[str] = []
        elif isinstance(raw_depends_on, list):
            depends_on = [str(dep).strip() for dep in raw_depends_on if str(dep).strip()]
        else:
            raise IssueWriterError(
                f"❌ ERROR: issue '{issue_id}' depends_on must be an array or null"
            )

        issues.append(
            PlannedIssue(
                issue_id=issue_id,
                repo=repo,
                title=title,
                description=str(raw_issue.get("description") or "").strip(),
                priority=(
                    str(raw_issue.get("priority") or "medium").strip() or "medium"
                ),
                depends_on=depends_on,
            )
        )

    return issues


def _dependency_order(issues: list[PlannedIssue]) -> list[str]:
    issue_by_id = {issue.issue_id: issue for issue in issues}

    indegree: dict[str, int] = {}
    forward: dict[str, list[str]] = {}
    for issue in issues:
        indegree[issue.issue_id] = 0
        forward[issue.issue_id] = []

    for issue in issues:
        for dep_id in issue.depends_on or []:
            if dep_id not in issue_by_id:
                raise IssueWriterError(
                    f"❌ ERROR: issue '{issue.issue_id}' depends_on unknown id '{dep_id}'"
                )
            if dep_id == issue.issue_id:
                raise IssueWriterError(
                    f"❌ ERROR: issue '{issue.issue_id}' cannot depend on itself"
                )
            indegree[issue.issue_id] += 1
            forward[dep_id].append(issue.issue_id)

    ready = [issue_id for issue_id, degree in indegree.items() if degree == 0]
    ordered: list[str] = []

    while ready:
        issue_id = ready.pop(0)
        ordered.append(issue_id)
        for dependent in forward.get(issue_id, []):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != len(issues):
        raise IssueWriterError("❌ ERROR: dependency cycle detected in ISSUES.json")

    return ordered


def _build_issue_body(
    *,
    session: PlanningSession,
    issue: PlannedIssue,
    blockers: list[CreatedIssue],
    project_slug: str,
) -> str:
    lines = [
        "# Planning Issue",
        "",
        f"Session: `{session.id}`",
        f"Mode: `{session.mode.value}`",
        f"Project: `{project_slug}`",
        "",
        "## Context",
        issue.description or "No additional description provided.",
    ]

    if blockers:
        lines.extend(["", "## Blocked By"])
        for blocker in blockers:
            lines.append(f"- `{blocker.issue_id}` ({blocker.repo})")
    return "\n".join(lines)


def _format_issue_repo(repo: str) -> tuple[str, str]:
    owner, name = repo.split("/", 1)
    return owner.strip(), name.strip()


async def write_issues_from_payload(
    *,
    session: PlanningSession,
    issues_json: str,
    project_slug: str,
    github_token: str,
) -> list[CreatedIssue]:
    """Create GitHub issues from planning ISSUES.json payload."""
    if not github_token.strip():
        raise IssueWriterError("❌ ERROR: GitHub token is required to create issues")

    issues = parse_issues_payload(issues_json)
    if not issues:
        return []

    ordered_issue_ids = _dependency_order(issues)
    issue_by_id = {issue.issue_id: issue for issue in issues}

    api_client = GitHubAPIClient(github_token)
    created: dict[str, CreatedIssue] = {}

    try:
        for issue_id in ordered_issue_ids:
            issue = issue_by_id[issue_id]
            owner, repo = _format_issue_repo(issue.repo)
            blockers = [created[dep] for dep in issue.depends_on or [] if dep in created]
            body = _build_issue_body(
                session=session,
                issue=issue,
                blockers=blockers,
                project_slug=project_slug,
            )
            payload = {
                "title": issue.title,
                "body": body,
            }
            response = await api_client.rest_post(
                f"/repos/{owner}/{repo}/issues",
                json=payload,
            )
            created[issue_id] = CreatedIssue(
                issue_id=issue_id,
                repo=f"{owner}/{repo}",
                title=issue.title,
                url=response.get("html_url", ""),
                number=int(response.get("number", 0)),
                blockers=[dep for dep in issue.depends_on or [] if dep in created],
            )
    finally:
        await api_client.close()

    return [created[issue_id] for issue_id in ordered_issue_ids]
