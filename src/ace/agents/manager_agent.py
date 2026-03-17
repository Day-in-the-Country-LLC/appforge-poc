"""Manager agent to select issues to start or resume."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import structlog

from ace.agents.llm_client import call_openai
from ace.config.secrets import resolve_github_token, resolve_openai_api_key
from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue, IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.planning.models import PlanningEvent, PlanningSession
from ace.planning.store_firestore import PlanningStore, build_planning_store
from ace.workspaces.git_ops import resolve_project_session_key

logger = structlog.get_logger(__name__)

_DEFAULT_TOOL_LOOP_MAX_STEPS = 6
_COORDINATOR_SESSION_PREFIX = "coordinator:"
_COORDINATOR_ACTIVE_STATUS = "coordinator_active"
_COORDINATOR_SESSION_REQUEST_TEXT = "project coordinator session"
_COORDINATOR_STATE_KEY = "coordinator"
_COORDINATOR_EVENT_NAME = "coordinator_plan_updated"


def _issue_key(owner: str | None, repo: str | None) -> str:
    return f"{owner or 'unknown'}/{repo or 'unknown'}"


def _build_issue_key(owner: str | None, repo: str | None, issue_number: int) -> str:
    return f"issue:{_issue_key(owner, repo)}#{issue_number}"


def _format_issue_key(issue: Issue) -> str:
    return _build_issue_key(issue.repo_owner, issue.repo_name, issue.number)


def _build_coordinator_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> PlanningEvent:
    return PlanningEvent(session_id=session_id, event_type=event_type, payload=payload)


def _build_project_summary(context_packs: dict[str, dict[str, Any]]) -> str:
    repos = sorted(
        {
            f"{pack.get('repo_owner', 'unknown')}/{pack.get('repo_name', 'unknown')}"
            for pack in context_packs.values()
            if isinstance(pack, dict)
        }
    )
    return (
        f"coordinator summary for {len(repos)} repos, "
        f"{len(context_packs)} total issues"
    )


def _issue_number_from_key(issue_key: str) -> int | None:
    if "#" not in issue_key:
        return None
    suffix = issue_key.split("#", 1)[1]
    if not suffix.isdigit():
        return None
    return int(suffix)


class ManagerAgent:
    """Selects which issues should be started or resumed."""

    def __init__(self, planning_store: PlanningStore | None = None) -> None:
        self.settings = get_settings()
        self._openai_key = resolve_openai_api_key(self.settings)
        self.model = self.settings.manager_agent_model or self.settings.codex_model
        self.skill_text = self._load_skill_text()
        self.tool_loop_enabled = self.settings.manager_agent_tool_loop_enabled
        self.tool_loop_max_steps = (
            self.settings.manager_agent_tool_loop_max_steps or _DEFAULT_TOOL_LOOP_MAX_STEPS
        )
        self._project_id: str | None = None
        self._planning_store: PlanningStore | None = planning_store
        github_token = resolve_github_token(self.settings)
        self._api_client = GitHubAPIClient(github_token)
        self._projects_client = ProjectsV2Client(self._api_client)
        self._issue_queue = IssueQueue(
            self._api_client,
            self.settings.github_org,
            "",
            self._projects_client,
        )

    def _resolve_project_session_key(self, project_slug: str | None = None) -> str:
        return resolve_project_session_key(
            explicit_project_session_key=project_slug,
            project_slug=project_slug,
            fallback_project_session_key=self.settings.agent_project_session_key,
            github_project_name=self.settings.github_project_name,
            gcp_project_id=self.settings.gcp_project_id,
        )

    def _coordinator_session_id(self, project_slug: str | None = None) -> str:
        return f"{_COORDINATOR_SESSION_PREFIX}{self._resolve_project_session_key(project_slug)}"

    async def _planning_store_or_error(self) -> PlanningStore:
        if self._planning_store is None:
            self._planning_store = build_planning_store(self.settings)
        return self._planning_store

    async def _load_coordinator_session(self, project_slug: str | None = None) -> PlanningSession:
        project_session_key = self._coordinator_session_id(project_slug)
        store = await self._planning_store_or_error()
        session = await store.get_session(project_session_key)
        if session is not None:
            return session

        now = datetime.now(UTC)
        session = PlanningSession(
            id=project_session_key,
            project_slug=project_session_key,
            request_text=_COORDINATOR_SESSION_REQUEST_TEXT,
            status=_COORDINATOR_ACTIVE_STATUS,
            intake_state={
                _COORDINATOR_STATE_KEY: {
                    "project_session_key": project_session_key,
                    "project_slug": self._resolve_project_session_key(project_slug),
                    "issue_order": [],
                    "context_packs": {},
                    "project_summary": "uninitialized",
                    "updated_at": now.isoformat(),
                }
            },
            created_at=now,
            updated_at=now,
        )
        await store.create_session(session)
        await store.append_event(
            session.id,
            _build_coordinator_event(
                session.id,
                "coordinator_session_created",
                {
                    "project_session_key": session.id,
                },
            ),
        )
        return session

    async def _persist_coordinator_state(
        self,
        session: PlanningSession,
        *,
        project_slug: str | None,
        issue_order: list[str],
        context_packs: dict[str, dict[str, Any]],
        category_counts: dict[str, int],
        project_repositories: list[str],
    ) -> None:
        store = await self._planning_store_or_error()
        incoming = session.intake_state.get(_COORDINATOR_STATE_KEY, {})
        if not isinstance(incoming, dict):
            incoming = {}

        incoming.update(
            {
                "project_session_key": session.id,
                "project_slug": self._resolve_project_session_key(project_slug),
                "issue_order": issue_order,
                "context_packs": context_packs,
                "project_summary": _build_project_summary(context_packs),
                "category_counts": category_counts,
                "project_repositories": project_repositories,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        session.intake_state[_COORDINATOR_STATE_KEY] = incoming
        session.updated_at = datetime.now(UTC)
        await store.update_session(session)
        await store.append_event(
            session.id,
            _build_coordinator_event(
                session.id,
                _COORDINATOR_EVENT_NAME,
                {
                    "project_session_key": session.id,
                    "issue_count": len(issue_order),
                    "category_counts": category_counts,
                },
            ),
        )

    async def _build_context_pack(
        self,
        issue: Issue,
        *,
        category: str,
        position: int,
        total: int,
        issue_order: list[str],
        category_counts: dict[str, int],
        project_repositories: list[str],
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        return {
            "issue_key": _format_issue_key(issue),
            "project_session_key": self._coordinator_session_id(project_slug),
            "project_slug": self._resolve_project_session_key(project_slug),
            "category": category,
            "position": position,
            "total": total,
            "issue_count_by_category": category_counts.copy(),
            "project_repositories": project_repositories,
            "preceding_issues": issue_order[:position - 1],
            "following_issues": issue_order[position:],
            "repo_owner": issue.repo_owner,
            "repo_name": issue.repo_name,
            "issue_title": issue.title,
            "labels": issue.labels,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    async def build_project_plan(
        self,
        in_progress: list[Issue],
        ready: list[Issue],
        *,
        project_slug: str | None = None,
    ) -> tuple[list[tuple[Issue, str]], dict[str, dict[str, Any]]]:
        """Build a project-coordinator view of work and persist durable state."""
        if not in_progress and not ready:
            return [], {}

        ordered_items: list[tuple[Issue, str]] = []
        work_meta_by_key: dict[str, dict[str, Any]] = {}
        work_items: list[dict[str, Any]] = []
        item_map: dict[str, Issue] = {}
        in_progress_map: dict[str, Issue] = {}
        ready_map: dict[str, Issue] = {}

        for issue in in_progress:
            key = _format_issue_key(issue)
            in_progress_map[key] = issue
            item_map[key] = issue
            work_items.append({"category": "in_progress", "issue": issue, "key": key})

        for issue in ready:
            key = _format_issue_key(issue)
            ready_map[key] = issue
            item_map[key] = issue
            work_items.append({"category": "ready", "issue": issue, "key": key})

        ordered_keys = await self.order_work_items(work_items)
        if not ordered_keys:
            ordered_keys = list(in_progress_map.keys()) + list(ready_map.keys())

        number_to_keys: dict[int, list[str]] = {}
        for key, issue in item_map.items():
            number_to_keys.setdefault(issue.number, []).append(key)

        normalized_ordered_keys: list[str] = []
        for key in ordered_keys:
            if key in item_map:
                normalized_ordered_keys.append(key)
                continue

            issue_number = _issue_number_from_key(key)
            if issue_number is None:
                continue

            candidates = [
                candidate
                for candidate in number_to_keys.get(issue_number, [])
                if candidate in item_map
            ]
            if len(candidates) == 1:
                normalized_ordered_keys.append(candidates[0])
        ordered_keys = normalized_ordered_keys

        for key in ordered_keys:
            issue = item_map.get(key)
            if issue is None:
                continue
            if key not in in_progress_map and key not in ready_map:
                continue
            ordered_items.append((issue, key))
            item_map.pop(key, None)

        for key, issue in item_map.items():
            ordered_items.append((issue, key))

        ordered_keys = [key for _, key in ordered_items]
        category_counts = {
            "in_progress": len(in_progress),
            "ready": len(ready),
            "total": len(ordered_items),
        }
        project_repositories = sorted(
            {
                _issue_key(issue.repo_owner, issue.repo_name)
                for issue, _ in ordered_items
                if issue.repo_owner and issue.repo_name
            }
        )

        context_packs: dict[str, dict[str, Any]] = {}
        for index, (issue, key) in enumerate(ordered_items, start=1):
            category = "ready"
            if key in in_progress_map:
                category = "in_progress"
            elif key in ready_map:
                category = "ready"

            pack = await self._build_context_pack(
                issue,
                category=category,
                position=index,
                total=len(ordered_items),
                issue_order=ordered_keys,
                category_counts=category_counts,
                project_repositories=project_repositories,
                project_slug=project_slug,
            )
            context_packs[key] = pack
            work_meta_by_key[key] = pack

        session = await self._load_coordinator_session(project_slug)
        await self._persist_coordinator_state(
            session,
            project_slug=project_slug,
            issue_order=ordered_keys,
            context_packs=context_packs,
            project_repositories=project_repositories,
            category_counts=category_counts,
        )
        return ordered_items, work_meta_by_key

    async def build_issue_context_pack(
        self,
        issue: Issue,
        *,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Build context for a single issue run (standalone trigger path)."""
        session = await self._load_coordinator_session(project_slug)
        raw_state = session.intake_state.get(_COORDINATOR_STATE_KEY, {})
        if not isinstance(raw_state, dict):
            raw_state = {}

        existing_order = raw_state.get("issue_order")
        issue_order: list[str] = []
        if isinstance(existing_order, list):
            issue_order = [str(item) for item in existing_order]

        issue_key = _format_issue_key(issue)
        try:
            position = issue_order.index(issue_key)
            position_index = position
        except ValueError:
            position_index = -1

        context = {
            "issue_key": issue_key,
            "project_session_key": session.id,
            "project_slug": self._resolve_project_session_key(project_slug),
            "category": "single",
            "position": position_index + 1 if position_index >= 0 else 1,
            "total": len(issue_order),
            "issue_count_by_category": raw_state.get("category_counts", {}),
            "project_repositories": raw_state.get("project_repositories", []),
            "preceding_issues": issue_order[: max(position_index, 0)],
            "following_issues": issue_order,
            "repo_owner": issue.repo_owner,
            "repo_name": issue.repo_name,
            "issue_title": issue.title,
            "labels": issue.labels,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        return context

    def _load_skill_text(self) -> str:
        path_value = self.settings.manager_skill_path
        if not path_value:
            return ""
        path = Path(path_value).expanduser()
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("manager_skill_missing", path=str(path))
        except Exception as exc:
            logger.warning("manager_skill_read_failed", path=str(path), error=str(exc))
        return ""

    def _format_issues(self, issues: Iterable[Issue]) -> str:
        payload = []
        for issue in issues:
            data = {
                "number": issue.number,
                "title": issue.title,
                "labels": issue.labels,
                "assignee": issue.assignee,
                "repo_owner": issue.repo_owner,
                "repo_name": issue.repo_name,
            }
            payload.append(data)
        return "\n".join([str(item) for item in payload])

    async def select_ready_issues(self, issues: list[Issue]) -> list[int]:
        """Select which ready issues should be started."""
        if not issues:
            return []
        if self.tool_loop_enabled:
            selected = await self._select_with_tools(
                "ready",
                issues,
                "Select issues that should be started now.",
            )
            if selected:
                return selected
        prompt = self._build_prompt(
            "ready",
            issues,
            "Select issues that should be started now. Return only a JSON array of issue numbers.",
        )
        return await self._call_and_parse(prompt, fallback=issues)

    async def select_resume_issues(self, issues: list[Issue]) -> list[int]:
        """Select which in-progress issues should be resumed."""
        if not issues:
            return []
        if self.tool_loop_enabled:
            selected = await self._select_with_tools(
                "in_progress",
                issues,
                "Select issues that should be resumed now.",
            )
            if selected:
                return selected
        prompt = self._build_prompt(
            "in_progress",
            issues,
            "Select issues that should be resumed now. Return only a JSON array of issue numbers.",
        )
        return await self._call_and_parse(prompt, fallback=issues)

    async def order_work_items(self, items: list[dict[str, Any]]) -> list[str]:
        """Order actionable work items by priority."""
        if not items:
            return []
        skill_section = f"\n\nSkill:\n{self.skill_text}\n" if self.skill_text else ""
        payload = []
        for item in items:
            issue = item.get("issue")
            if not isinstance(issue, Issue):
                continue
            payload.append(
                {
                    "key": item.get("key"),
                    "number": issue.number,
                    "title": issue.title,
                    "labels": issue.labels,
                    "repo_owner": issue.repo_owner,
                    "repo_name": issue.repo_name,
                    "category": item.get("category"),
                }
            )
        prompt = (
            "You are the Appforge manager agent. "
            "Order these work items in the exact sequence they should be processed.\n"
            "Priority order: in_progress first, then ready.\n"
            "Within a category, preserve sensible ordering based on urgency.\n"
            "Return ONLY a JSON array of item keys, in order.\n"
            f"{skill_section}\n"
            "Items:\n"
            f"{json.dumps(payload, indent=2)}\n"
        )
        try:
            response = await call_openai(
                prompt,
                self.model,
                self._openai_key,
                max_tokens=200,
                trace_name="manager_order_work_items",
                metadata={"item_count": len(payload)},
            )
            cleaned = (response or "").strip()
            parsed = _safe_parse_str_list(cleaned)
            if not parsed:
                raise ValueError("manager_order_empty")
            return parsed
        except Exception as exc:
            logger.warning("manager_order_failed", error=str(exc))
            return []

    def _build_prompt(self, mode: str, issues: list[Issue], instruction: str) -> str:
        skill_section = f"\n\nSkill:\n{self.skill_text}\n" if self.skill_text else ""
        issue_block = self._format_issues(issues)
        return (
            "You are the Appforge manager agent. "
            f"Mode: {mode}.\n"
            f"{instruction}\n"
            "Use the criteria from the skill and only select issues that match.\n"
            f"{skill_section}\n"
            "Issues:\n"
            f"{issue_block}\n\n"
            "Return ONLY a JSON array of issue numbers, e.g. [123, 456]."
        )

    async def _call_and_parse(self, prompt: str, fallback: list[Issue]) -> list[int]:
        try:
            response = await call_openai(
                prompt,
                self.model,
                self._openai_key,
                max_tokens=200,
                trace_name="manager_select",
                metadata={"issue_count": len(fallback)},
            )
            cleaned = (response or "").strip()
            if not cleaned.startswith("["):
                raise ValueError("manager_response_not_list")
            parsed = _safe_parse_int_list(cleaned)
            if not parsed:
                raise ValueError("manager_response_empty")
            return parsed
        except Exception as exc:
            logger.warning("manager_selection_failed", error=str(exc))
            return [issue.number for issue in fallback]

    async def _select_with_tools(
        self,
        mode: str,
        issues: list[Issue],
        instruction: str,
    ) -> list[int]:
        tool_history: list[dict[str, Any]] = []
        issue_block = self._format_issues(issues)
        skill_section = f"\n\nSkill:\n{self.skill_text}\n" if self.skill_text else ""
        tool_spec = (
            "Tools available:\n"
            "- get_issue {number, repo_owner, repo_name}\n"
            "- list_blockers {number, repo_owner, repo_name}\n"
            "- get_project_status {number, repo_owner, repo_name}\n"
            "\n"
            "Tool response format: JSON object with fields {tool, args, result}.\n"
            'Tool call format: {"action":"tool","tool":"<name>","args":{...}}.\n'
            'Done format: {"action":"done","selected":[1,2],"rationale":"..."}.\n'
            "Return ONLY a JSON object or JSON array.\n"
        )
        base_prompt = (
            "You are the Appforge manager agent. "
            f"Mode: {mode}.\n"
            f"{instruction}\n"
            "Use the criteria from the skill and only select issues that match.\n"
            "Use tools to resolve blockers or statuses when needed.\n"
            f"{skill_section}\n"
            f"{tool_spec}\n"
            "Issues:\n"
            f"{issue_block}\n\n"
        )

        prompt = base_prompt
        for _ in range(max(1, self.tool_loop_max_steps)):
            try:
                response = await call_openai(
                    prompt,
                    self.model,
                    self._openai_key,
                    max_tokens=400,
                    trace_name="manager_select_tools",
                    metadata={"issue_count": len(issues), "mode": mode},
                )
            except Exception as exc:
                logger.warning("manager_tool_loop_call_failed", error=str(exc))
                break

            parsed = _safe_parse_json(response)
            if parsed is None:
                logger.warning("manager_tool_loop_parse_failed", response=response[:200])
                break

            if isinstance(parsed, list):
                return _safe_parse_int_list(json.dumps(parsed))

            action = parsed.get("action")
            if action == "done":
                selected = parsed.get("selected", [])
                return _safe_parse_int_list(json.dumps(selected))
            if action != "tool":
                logger.warning("manager_tool_loop_unknown_action", action=action)
                break

            tool_name = parsed.get("tool", "")
            args = parsed.get("args", {}) if isinstance(parsed.get("args"), dict) else {}
            result = await self._call_tool(tool_name, args)
            tool_history.append({"tool": tool_name, "args": args, "result": result})
            prompt = (
                base_prompt
                + "Tool history:\n"
                + json.dumps(tool_history, indent=2, default=str)
                + "\n\nContinue.\n"
            )

        return []

    async def _call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if tool_name == "get_issue":
                issue_number = _require_int_arg(args, "number", tool_name)
                repo_owner = _require_str_arg(args, "repo_owner", tool_name)
                repo_name = _require_str_arg(args, "repo_name", tool_name)
                issue = await self._issue_queue.get_issue(
                    issue_number,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                )
                return {
                    "number": issue.number,
                    "title": issue.title,
                    "labels": issue.labels,
                    "assignee": issue.assignee,
                    "state": issue.state,
                }
            if tool_name == "list_blockers":
                repo_owner = _require_str_arg(args, "repo_owner", tool_name)
                repo_name = _require_str_arg(args, "repo_name", tool_name)
                issue_number = _require_int_arg(args, "number", tool_name)
                blockers = await self._projects_client.get_issue_blockers(
                    repo_owner,
                    repo_name,
                    issue_number,
                )
                return [
                    {
                        "number": blocker.number,
                        "title": blocker.title,
                        "state": blocker.state,
                        "repo_owner": blocker.repo_owner,
                        "repo_name": blocker.repo_name,
                    }
                    for blocker in blockers
                ]
            if tool_name == "get_project_status":
                issue_number = _require_int_arg(args, "number", tool_name)
                repo_owner = _require_str_arg(args, "repo_owner", tool_name)
                repo_name = _require_str_arg(args, "repo_name", tool_name)
                project_id = await self._get_project_id()
                status = await self._projects_client.get_issue_project_status(
                    project_id,
                    issue_number,
                    repo_owner,
                    repo_name,
                )
                return {"status": status}
        except Exception as exc:
            logger.warning("manager_tool_call_failed", tool=tool_name, error=str(exc))
            return {"error": str(exc)}
        return {"error": f"unknown_tool:{tool_name}"}

    async def _get_project_id(self) -> str:
        if self._project_id:
            return self._project_id
        project_id = await self._projects_client.get_org_project_id(
            self.settings.github_org,
            self.settings.github_project_name,
        )
        if not project_id:
            raise ValueError(
                f"Project '{self.settings.github_project_name}' not found in org "
                f"'{self.settings.github_org}'"
            )
        self._project_id = project_id
        return project_id


def _require_int_arg(args: dict[str, Any], key: str, tool_name: str) -> int:
    value = args.get(key)
    if value is None:
        raise ValueError(f"{tool_name} tool requires integer field '{key}'")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{tool_name} tool requires integer field '{key}', got {type(value).__name__}"
        ) from None


def _require_str_arg(args: dict[str, Any], key: str, tool_name: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{tool_name} tool requires non-empty string field '{key}'"
        )
    return value

def _safe_parse_int_list(raw: str) -> list[int]:
    """Parse a JSON list into integers, ignoring invalid items."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`\n ")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[-1].strip()
    if not cleaned.startswith("[") or not cleaned.endswith("]"):
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    values: list[int] = []
    for item in parsed:
        if isinstance(item, bool):
            continue
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


def _safe_parse_str_list(raw: str) -> list[str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`\n ")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[-1].strip()
    if not cleaned.startswith("[") or not cleaned.endswith("]"):
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _safe_parse_json(raw: str) -> dict | list | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`\n ")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[-1].strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    return None
