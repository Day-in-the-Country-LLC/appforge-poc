"""Manager agent to select issues to start or resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Any

import structlog

from ace.agents.llm_client import call_openai
from ace.config.secrets import resolve_github_token
from ace.config.secrets import resolve_openai_api_key
from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue
from ace.github.issue_queue import IssueQueue
from ace.github.projects_v2 import ProjectsV2Client

logger = structlog.get_logger(__name__)

_DEFAULT_TOOL_LOOP_MAX_STEPS = 6


class ManagerAgent:
    """Selects which issues should be started or resumed."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._openai_key = resolve_openai_api_key(self.settings)
        self.model = self.settings.manager_agent_model or self.settings.codex_model
        self.skill_text = self._load_skill_text()
        self.tool_loop_enabled = self.settings.manager_agent_tool_loop_enabled
        self.tool_loop_max_steps = (
            self.settings.manager_agent_tool_loop_max_steps
            or _DEFAULT_TOOL_LOOP_MAX_STEPS
        )
        self._project_id: str | None = None
        github_token = resolve_github_token(self.settings)
        self._api_client = GitHubAPIClient(github_token)
        self._projects_client = ProjectsV2Client(self._api_client)
        self._issue_queue = IssueQueue(
            self._api_client,
            self.settings.github_org,
            "",
            self._projects_client,
        )

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
            "Tool call format: {\"action\":\"tool\",\"tool\":\"<name>\",\"args\":{...}}.\n"
            "Done format: {\"action\":\"done\",\"selected\":[1,2],\"rationale\":\"...\"}.\n"
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
                issue = await self._issue_queue.get_issue(
                    int(args.get("number")),
                    repo_owner=args.get("repo_owner"),
                    repo_name=args.get("repo_name"),
                )
                return {
                    "number": issue.number,
                    "title": issue.title,
                    "labels": issue.labels,
                    "assignee": issue.assignee,
                    "state": issue.state,
                }
            if tool_name == "list_blockers":
                blockers = await self._projects_client.get_issue_blockers(
                    args.get("repo_owner"),
                    args.get("repo_name"),
                    int(args.get("number")),
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
                project_id = await self._get_project_id()
                status = await self._projects_client.get_issue_project_status(
                    project_id,
                    int(args.get("number")),
                    args.get("repo_owner"),
                    args.get("repo_name"),
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


def _safe_parse_int_list(raw: str) -> list[int]:
    """Parse a JSON-like list of ints without pulling in a full JSON parser."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`\n ")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[-1].strip()
    if not cleaned.startswith("[") or not cleaned.endswith("]"):
        return []
    inner = cleaned[1:-1].strip()
    if not inner:
        return []
    values = []
    for part in inner.split(","):
        part = part.strip().strip("\"")
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            continue
    return values


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
