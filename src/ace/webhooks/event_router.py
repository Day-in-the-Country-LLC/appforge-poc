"""Webhook event normalization for work-routing events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_GITHUB_EVENTS = {
    "projects_v2_item",
    "issues",
    "issue_comment",
    "pull_request",
}


@dataclass(frozen=True)
class WorkEvent:
    """Normalized webhook event envelope."""

    source: str
    event_type: str
    action: str | None = None
    delivery: str | None = None
    repository_owner: str | None = None
    repository_name: str | None = None
    issue_number: int | None = None
    item_node_id: str | None = None
    project_node_id: str | None = None
    content_node_id: str | None = None
    status_from: str | None = None
    status_to: str | None = None
    is_pull_request_comment: bool = False
    project_name: str | None = None

    @property
    def event(self) -> str:
        return self.event_type

    @property
    def repo_owner(self) -> str | None:
        return self.repository_owner

    @property
    def repo_name(self) -> str | None:
        return self.repository_name

    @property
    def project_id(self) -> str | None:
        return self.project_node_id

    @property
    def project_item_id(self) -> str | None:
        return self.item_node_id

    @property
    def is_pull_request(self) -> bool:
        return self.is_pull_request_comment

    @property
    def transition_from(self) -> str | None:
        return self.status_from

    @property
    def transition_to(self) -> str | None:
        return self.status_to

    @property
    def is_comment_event(self) -> bool:
        return self.event_type == "issue_comment"

    @property
    def is_issue_closed_event(self) -> bool:
        return self.event_type == "issues" and (self.action or "").lower() == "closed"

    @property
    def is_github_ready_transition(self) -> bool:
        if not self.status_from or not self.status_to:
            return False
        return (
            self.status_from.strip().lower() == "backlog"
            and self.status_to.strip().lower() == "ready"
        )

    @property
    def is_github_in_progress_transition(self) -> bool:
        if not self.status_from or not self.status_to:
            return False
        return (
            self.status_from.strip().lower() == "blocked"
            and self.status_to.strip().lower() == "in progress"
        )

    @property
    def is_supported(self) -> bool:
        return self.source == "github" and self.event_type in SUPPORTED_GITHUB_EVENTS

    @property
    def is_ignored(self) -> bool:
        return self.event_type == "unsupported"


class WorkEventRouter:
    """Normalize webhook payloads into a stable event representation."""

    @staticmethod
    def route(
        event: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "github",
        delivery: str | None = None,
        default_project: str | None = None,
    ) -> WorkEvent | None:
        if event is None or payload is None:
            return None

        normalized_source = (source or "github").lower().strip()
        event_name = event.strip().lower()

        if normalized_source != "github":
            return (
                WorkEventRouter._route_linear(payload, event_name)
                or WorkEvent(
                    source=normalized_source,
                    event_type="unsupported",
                    action=payload.get("action"),
                    delivery=delivery,
                )
            )

        if event_name in SUPPORTED_GITHUB_EVENTS:
            method = {
                "projects_v2_item": WorkEventRouter._route_projects_v2_item,
                "issues": WorkEventRouter._route_issues,
                "issue_comment": WorkEventRouter._route_issue_comment,
                "pull_request": WorkEventRouter._route_pull_request,
            }[event_name]
            return method(payload, default_project=default_project, delivery=delivery)

        linear = WorkEventRouter._route_linear(payload, event_name)
        if linear is not None:
            return linear

        return WorkEvent(
            source="github",
            event_type="unsupported",
            action=payload.get("action"),
            delivery=delivery,
        )

    @staticmethod
    def _route_projects_v2_item(
        payload: dict[str, Any],
        *,
        default_project: str | None = None,
        delivery: str | None = None,
    ) -> WorkEvent:
        item = _extract_projects_item(payload)
        project_node = payload.get("project") or {}
        project_id = _extract_project_node_id(payload)
        project_name = _extract_project_title(project_node)
        if not project_name:
            project_name = default_project

        item_node_id = _extract_item_node_id(item)
        content_node_id = _extract_content_node_id(item)
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        status_from, status_to = _extract_status_transition(payload)

        return WorkEvent(
            source="github",
            event_type="projects_v2_item",
            action=payload.get("action"),
            delivery=delivery,
            repository_owner=repo_owner,
            repository_name=repo_name,
            item_node_id=item_node_id,
            project_node_id=project_id,
            content_node_id=content_node_id,
            status_from=status_from,
            status_to=status_to,
            project_name=project_name,
        )

    @staticmethod
    def _route_issue_comment(payload: dict[str, Any], **_) -> WorkEvent:
        issue = payload.get("issue") or {}
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        number = issue.get("number")

        return WorkEvent(
            source="github",
            event_type="issue_comment",
            action=payload.get("action"),
            delivery=None,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=int(number) if isinstance(number, int) else _safe_int(number),
            is_pull_request_comment=bool(issue.get("pull_request")),
        )

    @staticmethod
    def _route_issues(payload: dict[str, Any], **_) -> WorkEvent:
        issue = payload.get("issue") or {}
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        return WorkEvent(
            source="github",
            event_type="issues",
            action=payload.get("action"),
            delivery=None,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=_safe_int(issue.get("number")),
        )

    @staticmethod
    def _route_pull_request(payload: dict[str, Any], **_) -> WorkEvent:
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        number = payload.get("number")
        if number is None:
            pull_request = payload.get("pull_request") or {}
            number = pull_request.get("number")
        return WorkEvent(
            source="github",
            event_type="pull_request",
            action=payload.get("action"),
            delivery=None,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=_safe_int(number),
        )

    @staticmethod
    def _route_linear(payload: dict[str, Any], event_name: str) -> WorkEvent | None:
        source_event = (
            _first_str(payload, ("event", "type"))
            or event_name
        )
        if not source_event or not source_event.startswith("linear"):
            return None

        issue = payload.get("issue") or payload.get("data") or {}
        if not isinstance(issue, dict):
            return None

        repo = issue.get("repository") or {}
        if not isinstance(repo, dict):
            repo = {}
        team = payload.get("team") or {}
        if not isinstance(team, dict):
            team = {}

        transition_from: str | None = None
        transition_to: str | None = None
        next_state = issue.get("state")
        if isinstance(next_state, dict):
            transition_to = next_state.get("name")
        elif next_state is not None:
            transition_to = str(next_state)

        previous_values = payload.get("previousValues") or {}
        if isinstance(previous_values, dict):
            previous_state = previous_values.get("state")
            if isinstance(previous_state, dict):
                transition_from = previous_state.get("name")
            elif previous_state is not None:
                transition_from = str(previous_state)

        repo_owner = (
            _nested_get(repo, ("organization", "key"))
            or _nested_get(repo, ("organization", "name"))
            or _nested_get(repo, ("key"))
            or _nested_get(repo, ("owner", "login"))
        )

        return WorkEvent(
            source="linear",
            event_type=source_event,
            action=payload.get("action"),
            issue_number=_safe_int(issue.get("number")),
            repository_owner=repo_owner,
            repository_name=_nested_get(repo, ("name",)),
            project_node_id=_first_str(team, ("id", "teamId")),
            project_name=team.get("name") if isinstance(team, dict) else None,
            status_from=_safe_status_value(transition_from),
            status_to=_safe_status_value(transition_to),
        )


def _extract_projects_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item")
    if not isinstance(item, dict):
        raise ValueError("❌ ERROR: webhook payload missing projects_v2_item")
    return item


def _extract_project_node_id(payload: dict[str, Any]) -> str | None:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    project = payload.get("project") or {}
    return _first_str(
        item,
        ("project_node_id", "projectNodeId", "project_id", "projectId"),
        project=project,
    )


def _extract_project_title(project: Any) -> str | None:
    if not isinstance(project, dict):
        return None
    return _first_str(project, ("title", "name", "project_title", "projectName"))


def _extract_item_node_id(item: dict[str, Any]) -> str | None:
    return _first_str(
        item,
        ("node_id", "nodeId", "item_node_id", "itemNodeId", "id"),
    )


def _extract_content_node_id(item: dict[str, Any]) -> str | None:
    content_node_id = _first_str(item, ("content_node_id", "contentNodeId"))
    if content_node_id:
        return content_node_id

    content = item.get("content")
    if not isinstance(content, dict):
        return None
    return _first_str(content, ("node_id", "nodeId", "id"))


def _extract_status_transition(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict):
        return (None, None)

    field_value = changes.get("field_value") or changes.get("fieldValue") or {}
    if not isinstance(field_value, dict):
        return (None, None)

    field_name = (
        field_value.get("field_name")
        or field_value.get("fieldName")
        or field_value.get("field")
    )
    if field_name and str(field_name).lower() != "status":
        return (None, None)

    from_value = field_value.get("from") or field_value.get("from_value")
    to_value = field_value.get("to") or field_value.get("to_value")
    return (_safe_status_value(from_value), _safe_status_value(to_value))


def _extract_repository_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    repository = payload.get("repository") or {}
    if not isinstance(repository, dict):
        return None, None
    owner = (repository.get("owner") or {}).get("login")
    name = repository.get("name")
    if not isinstance(owner, str) or not owner.strip():
        return None, None
    if not isinstance(name, str) or not name.strip():
        return None, None
    return owner.strip(), name.strip()


def _first_str(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    project: dict[str, Any] | None = None,
) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if project is not None:
        for key in keys:
            value = project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _safe_status_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "option", "label"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return str(value).strip() if str(value) else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_get(value: Any, path: tuple[str, ...], default: str | None = None) -> str | None:
    node = value
    for part in path:
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    if isinstance(node, str) and node.strip():
        return node.strip()
    return default
