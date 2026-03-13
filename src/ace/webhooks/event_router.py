"""Event router and normalized work-event model for webhook ingress."""

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
    """Normalized webhook work event."""

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

    @property
    def is_supported(self) -> bool:
        return self.source == "github" and self.event_type in SUPPORTED_GITHUB_EVENTS

    @property
    def is_ignored(self) -> bool:
        return self.event_type == "unsupported"


class WorkEventRouter:
    """Normalize inbound GitHub payload shapes to a stable work event."""

    def route(
        self,
        *,
        source: str,
        event: str,
        payload: dict[str, Any],
        delivery: str | None = None,
    ) -> WorkEvent:
        if source != "github":
            return WorkEvent(
                source=source,
                event_type="unsupported",
                delivery=delivery,
            )

        if event == "projects_v2_item":
            return self._route_projects_v2_item(payload, delivery)
        if event == "issues":
            return self._route_issue_event(payload, delivery)
        if event == "issue_comment":
            return self._route_issue_comment(payload, delivery)
        if event == "pull_request":
            return self._route_pull_request(payload, delivery)

        return WorkEvent(
            source="github",
            event_type="unsupported",
            action=None,
            delivery=delivery,
        )

    def _route_projects_v2_item(
        self,
        payload: dict[str, Any],
        delivery: str | None,
    ) -> WorkEvent:
        item = _extract_projects_item(payload)
        project_id = _extract_project_node_id(payload)
        item_id = _extract_item_node_id(item)
        content_node_id = _extract_content_node_id(item)
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        status_from, status_to = _extract_status_transition(payload)
        return WorkEvent(
            source="github",
            event_type="projects_v2_item",
            action=payload.get("action", None),
            delivery=delivery,
            repository_owner=repo_owner,
            repository_name=repo_name,
            item_node_id=item_id,
            project_node_id=project_id,
            content_node_id=content_node_id,
            status_from=status_from,
            status_to=status_to,
        )

    def _route_issue_event(
        self,
        payload: dict[str, Any],
        delivery: str | None,
    ) -> WorkEvent:
        issue = payload.get("issue") or {}
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        number = issue.get("number")
        return WorkEvent(
            source="github",
            event_type="issues",
            action=payload.get("action"),
            delivery=delivery,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=int(number) if isinstance(number, int) else None,
        )

    def _route_issue_comment(
        self,
        payload: dict[str, Any],
        delivery: str | None,
    ) -> WorkEvent:
        issue = payload.get("issue") or {}
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        number = issue.get("number")
        return WorkEvent(
            source="github",
            event_type="issue_comment",
            action=payload.get("action"),
            delivery=delivery,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=int(number) if isinstance(number, int) else None,
            is_pull_request_comment=bool(issue.get("pull_request")),
        )

    def _route_pull_request(
        self,
        payload: dict[str, Any],
        delivery: str | None,
    ) -> WorkEvent:
        repo_owner, repo_name = _extract_repository_from_payload(payload)
        number = payload.get("number")
        if number is None:
            pull_request = payload.get("pull_request") or {}
            number = pull_request.get("number")
        return WorkEvent(
            source="github",
            event_type="pull_request",
            action=payload.get("action"),
            delivery=delivery,
            repository_owner=repo_owner,
            repository_name=repo_name,
            issue_number=int(number) if isinstance(number, int) else None,
        )


def _extract_projects_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    if not isinstance(item, dict):
        raise ValueError("❌ ERROR: webhook payload missing projects_v2_item")
    return item


def _extract_project_node_id(payload: dict[str, Any]) -> str | None:
    item = payload.get("projects_v2_item") or payload.get("project_v2_item") or {}
    project = payload.get("project") or {}
    for key in ("project_node_id", "projectNodeId", "project_id", "projectId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("node_id", "nodeId", "id"):
        value = project.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_item_node_id(item: dict[str, Any]) -> str | None:
    for key in ("node_id", "nodeId", "item_node_id", "itemNodeId", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_content_node_id(item: dict[str, Any]) -> str | None:
    for key in ("content_node_id", "contentNodeId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    content = item.get("content") or {}
    if not isinstance(content, dict):
        return None
    for key in ("node_id", "nodeId", "id"):
        value = content.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_status_transition(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict):
        return None, None
    field_value = changes.get("field_value") or changes.get("fieldValue") or {}
    if not isinstance(field_value, dict):
        return None, None
    field_name = field_value.get("field_name") or field_value.get("fieldName")
    if field_name and str(field_name).lower() != "status":
        return None, None
    from_value = field_value.get("from") or field_value.get("from_value")
    to_value = field_value.get("to") or field_value.get("to_value")
    from_status = _normalize_status(from_value)
    to_status = _normalize_status(to_value)
    return from_status, to_status


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "option", "label"):
            if value.get(key):
                return str(value.get(key))
        return None
    if isinstance(value, str):
        return value
    return str(value)


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
