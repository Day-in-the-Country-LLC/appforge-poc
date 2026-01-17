"""Sequential task planning and instruction generation."""

from __future__ import annotations

import json
import asyncio
import subprocess
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ace.agents.llm_client import call_claude, call_openai
from ace.config.settings import get_settings
from ace.github.issue_queue import Issue

logger = structlog.get_logger(__name__)

TASKS_FILENAME = "ace_tasks.json"
INSTRUCTIONS_FILENAME = "ACE_TASK.md"
DONE_FILENAME = "ACE_TASK_DONE.json"


@dataclass
class TaskItem:
    """Represents a sequential task for an issue."""

    task_id: str
    title: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done
    completion: dict[str, Any] | None = None


@dataclass
class TaskPlan:
    """Plan for an issue."""

    issue_number: int
    issue_title: str
    tasks: list[TaskItem]
    created_at: str
    updated_at: str
    pr_number: int | None = None
    pr_url: str | None = None
    plan_comment_id: int | None = None
    last_validation_task_id: str | None = None
    last_validation_error: str | None = None


class TaskValidationError(RuntimeError):
    """Raised when task completion validation fails."""

    def __init__(self, message: str, task_id: str, task_title: str) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.task_title = task_title


class TaskPlanner:
    """Generates a sequential task list for an issue."""

    def __init__(self, backend: str, model: str | None = None) -> None:
        self.backend = backend.lower()
        self.model = model
        self.settings = get_settings()

    async def plan(self, issue: Issue) -> list[TaskItem]:
        prompt = self._build_prompt(issue)
        try:
            output = await self._call_model(
                prompt,
                trace_name="task_plan",
                metadata={
                    "issue_number": issue.number,
                    "issue_title": issue.title,
                },
            )
            tasks = self._parse_tasks(output)
            if tasks:
                return tasks
        except Exception as e:
            logger.warning("task_plan_failed", error=str(e))

        return [
            TaskItem(
                task_id="task-1",
                title=issue.title.strip() or "Complete issue",
                description="Complete the issue as described.",
                acceptance_criteria=["All requirements in the issue are implemented."],
            )
        ]

    def _build_prompt(self, issue: Issue) -> str:
        return f"""
You are a project manager. Break the issue into sequential, low-conflict tasks.

Issue Title: {issue.title}
Issue Body:
{issue.body}

Return ONLY valid JSON using this shape:
{{
  "tasks": [
    {{
      "title": "Short task title",
      "description": "What to do",
      "acceptance_criteria": ["bullet 1", "bullet 2"]
    }}
  ]
}}

Rules:
- 1 to 5 tasks.
- Tasks must be sequential (no parallel assumptions).
- Keep descriptions concise and actionable.
"""

    async def _call_model(
        self,
        prompt: str,
        *,
        trace_name: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        if self.backend == "claude":
            return await call_claude(
                prompt,
                self.model or self.settings.claude_model,
                self.settings.claude_api_key,
                max_tokens=800,
                trace_name=trace_name,
                metadata=metadata,
            )
        return await call_openai(
            prompt,
            self.model or self.settings.codex_model,
            self.settings.openai_api_key,
            max_tokens=800,
            trace_name=trace_name,
            metadata=metadata,
        )

    def _parse_tasks(self, output: str) -> list[TaskItem]:
        payload = _extract_json(output)
        if not payload:
            return []

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []

        raw_tasks = data.get("tasks", [])
        tasks: list[TaskItem] = []
        for idx, item in enumerate(raw_tasks, start=1):
            title = str(item.get("title", f"Task {idx}")).strip()
            description = str(item.get("description", "")).strip()
            acceptance = item.get("acceptance_criteria") or []
            if not isinstance(acceptance, list):
                acceptance = [str(acceptance)]
            tasks.append(
                TaskItem(
                    task_id=f"task-{idx}",
                    title=title,
                    description=description,
                    acceptance_criteria=[str(a).strip() for a in acceptance if str(a).strip()],
                )
            )

        return tasks


class InstructionBuilder:
    """Creates detailed instructions for a single task."""

    def __init__(self, backend: str, model: str | None = None) -> None:
        self.backend = backend.lower()
        self.model = model
        self.settings = get_settings()

    async def build(self, issue: Issue, task: TaskItem) -> str:
        prompt = self._build_prompt(issue, task)
        try:
            return await self._call_model(
                prompt,
                trace_name="task_instructions",
                metadata={
                    "issue_number": issue.number,
                    "issue_title": issue.title,
                    "task_id": task.task_id,
                    "task_title": task.title,
                },
            )
        except Exception as e:
            logger.warning("instruction_build_failed", error=str(e))
            return self._fallback_instructions(issue, task)

    def _build_prompt(self, issue: Issue, task: TaskItem) -> str:
        return f"""
You are an instruction agent. Write detailed, step-by-step coding instructions
for the task below. Output Markdown only.

Issue Title: {issue.title}
Issue Body:
{issue.body}

Task Title: {task.title}
Task Description: {task.description}
Acceptance Criteria: {task.acceptance_criteria}

Include:
- Key files/areas to inspect
- Concrete steps
- Validation/tests to run
"""

    async def _call_model(
        self,
        prompt: str,
        *,
        trace_name: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        if self.backend == "claude":
            return await call_claude(
                prompt,
                self.model or self.settings.claude_model,
                self.settings.claude_api_key,
                max_tokens=1200,
                trace_name=trace_name,
                metadata=metadata,
            )
        return await call_openai(
            prompt,
            self.model or self.settings.codex_model,
            self.settings.openai_api_key,
            max_tokens=1200,
            trace_name=trace_name,
            metadata=metadata,
        )

    def _fallback_instructions(self, issue: Issue, task: TaskItem) -> str:
        criteria = "\n".join(f"- {item}" for item in task.acceptance_criteria) or "- N/A"
        return (
            f"## Task\n{task.title}\n\n"
            f"{task.description}\n\n"
            "## Acceptance Criteria\n"
            f"{criteria}\n\n"
            "## Steps\n"
            "1. Review the issue details and repo context.\n"
            "2. Implement the changes for this task.\n"
            "3. Run relevant tests and validate behavior.\n"
        )


class TaskManager:
    """Manages sequential tasks within a worktree."""

    def __init__(self, worktree_path: Path) -> None:
        self.worktree_path = worktree_path
        self.tasks_path = worktree_path / TASKS_FILENAME
        self.instructions_path = worktree_path / INSTRUCTIONS_FILENAME
        self.done_path = worktree_path / DONE_FILENAME

    async def load_or_create_plan(
        self,
        issue: Issue,
        backend: str,
        model: str | None,
    ) -> tuple[TaskPlan, bool]:
        plan = self.load_plan()
        if plan:
            return plan, False

        planner = TaskPlanner(backend, model)
        tasks = await planner.plan(issue)
        now = datetime.utcnow().isoformat()
        plan = TaskPlan(
            issue_number=issue.number,
            issue_title=issue.title,
            tasks=tasks,
            created_at=now,
            updated_at=now,
            pr_number=None,
            pr_url=None,
            plan_comment_id=None,
            last_validation_task_id=None,
            last_validation_error=None,
        )
        self.save_plan(plan)
        return plan, True

    def load_plan(self) -> TaskPlan | None:
        if not self.tasks_path.exists():
            return None

        try:
            data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
            tasks = [
                TaskItem(
                    task_id=item["task_id"],
                    title=item["title"],
                    description=item.get("description", ""),
                    acceptance_criteria=item.get("acceptance_criteria", []),
                    status=item.get("status", "pending"),
                    completion=item.get("completion"),
                )
                for item in data.get("tasks", [])
            ]
            return TaskPlan(
                issue_number=data["issue_number"],
                issue_title=data["issue_title"],
                tasks=tasks,
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                pr_number=data.get("pr_number"),
                pr_url=data.get("pr_url"),
                plan_comment_id=data.get("plan_comment_id"),
                last_validation_task_id=data.get("last_validation_task_id"),
                last_validation_error=data.get("last_validation_error"),
            )
        except Exception as e:
            logger.warning("task_plan_load_failed", error=str(e))
            return None

    def save_plan(self, plan: TaskPlan) -> None:
        plan.updated_at = datetime.utcnow().isoformat()
        payload = {
            "issue_number": plan.issue_number,
            "issue_title": plan.issue_title,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "pr_number": plan.pr_number,
            "pr_url": plan.pr_url,
            "plan_comment_id": plan.plan_comment_id,
            "last_validation_task_id": plan.last_validation_task_id,
            "last_validation_error": plan.last_validation_error,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "description": t.description,
                    "acceptance_criteria": t.acceptance_criteria,
                    "status": t.status,
                    "completion": t.completion,
                }
                for t in plan.tasks
            ],
        }
        self.tasks_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def apply_done_marker(self, plan: TaskPlan, branch_name: str) -> TaskItem | None:
        marker = self._read_done_marker()
        if not marker:
            return None

        task_id = marker.get("task_id")
        if not task_id:
            return None

        for task in plan.tasks:
            if task.task_id == task_id:
                self._validate_completion(task, branch_name)
                task.status = "done"
                task.completion = marker
                plan.last_validation_task_id = None
                plan.last_validation_error = None
                break
        else:
            return None

        self.done_path.unlink(missing_ok=True)
        self.save_plan(plan)
        return task

    def current_task(self, plan: TaskPlan) -> TaskItem | None:
        for task in plan.tasks:
            if task.status == "in_progress":
                return task
        for task in plan.tasks:
            if task.status == "pending":
                return task
        return None

    def mark_in_progress(self, plan: TaskPlan, task_id: str) -> None:
        for task in plan.tasks:
            if task.task_id == task_id:
                task.status = "in_progress"
                self.save_plan(plan)
                return

    def write_instructions(
        self,
        issue: Issue,
        task: TaskItem,
        content: str,
        branch_name: str,
    ) -> None:
        blocked_assignee = get_settings().blocked_assignee
        agent_label = get_settings().github_agent_label

        mcp_block = (
            "\n\n## GitHub MCP Access\n"
            "GitHub MCP is configured for this session. Use it for issue comments/metadata as needed.\n"
        )

        completion_block = (
            "\n\n## Completion Protocol\n"
            "When finished:\n"
            f"1. Commit changes on `{branch_name}` with a message that includes `{task.title}`.\n"
            f"2. Push the branch: `git push origin {branch_name}`.\n"
            "3. Write a JSON file named `ACE_TASK_DONE.json` in the repo root:\n\n"
            "```json\n"
            "{\n"
            f"  \"task_id\": \"{task.task_id}\",\n"
            "  \"summary\": \"<summary>\",\n"
            "  \"files_changed\": [\"...\"],\n"
            "  \"commands_run\": [\"...\"]\n"
            "}\n"
            "```\n"
            "Do NOT open a PR; the manager will open it after all tasks are complete.\n"
        )

        blocked_block = (
            "\n\n## Blocked Protocol (No Questions in Session)\n"
            "If clarification is needed:\n"
            "1. Post a GitHub comment with your questions (prefix with BLOCKED).\n"
            f"2. Assign the issue to {blocked_assignee} and remove the '{agent_label}' label.\n"
            "3. Exit the session.\n"
        )

        header = f"# Task {task.task_id}: {task.title}\n\n"
        body = content.strip()
        document = f"{header}{body}{mcp_block}{blocked_block}{completion_block}"
        self.instructions_path.write_text(document, encoding="utf-8")

    def _read_done_marker(self) -> dict[str, Any] | None:
        if not self.done_path.exists():
            return None
        try:
            return json.loads(self.done_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("done_marker_parse_failed", error=str(e))
            return None

    def read_done_marker(self) -> dict[str, Any] | None:
        """Return the current done marker contents without side effects."""
        return self._read_done_marker()

    def progress_signature(self) -> str:
        """Return a lightweight signature for detecting repo progress."""
        head = self._git_raw(["rev-parse", "HEAD"])
        status = self._git_raw(["status", "--porcelain"])
        return f"{head}\n{status}".strip()

    def _validate_completion(self, task: TaskItem, branch_name: str) -> None:
        if self._git_status_dirty(task):
            raise TaskValidationError(
                "Working tree is dirty. Commit and clean the worktree.",
                task.task_id,
                task.title,
            )

        commit_message = self._git_log_message(task)
        if task.title.lower() not in commit_message.lower():
            raise TaskValidationError(
                f"Latest commit message must include task title: '{task.title}'.",
                task.task_id,
                task.title,
            )

        if not self._branch_pushed(branch_name, task):
            raise TaskValidationError(
                f"Branch '{branch_name}' is not pushed to origin.",
                task.task_id,
                task.title,
            )

    def _git_status_dirty(self, task: TaskItem) -> bool:
        result = self._git(["status", "--porcelain"], task)
        return bool(result.strip())

    def _git_log_message(self, task: TaskItem) -> str:
        return self._git(["log", "-1", "--pretty=%B"], task).strip()

    def _branch_pushed(self, branch_name: str, task: TaskItem) -> bool:
        fetch = subprocess.run(
            ["git", "-C", str(self.worktree_path), "fetch", "origin", branch_name],
            check=False,
            capture_output=True,
            timeout=120,
        )
        if fetch.returncode != 0:
            stderr = fetch.stderr.decode("utf-8", errors="replace").strip()
            detail = f"Could not fetch origin/{branch_name}. Push the branch."
            if stderr:
                detail = f"{detail} {stderr}"
            raise TaskValidationError(
                detail,
                task.task_id,
                task.title,
            )

        local = self._git(["rev-parse", "HEAD"], task).strip()
        remote = self._git(["rev-parse", f"origin/{branch_name}"], task).strip()
        return local == remote

    def _git(self, args: list[str], task: TaskItem | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.worktree_path)] + args,
            check=False,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            message = f"Git command failed: {' '.join(args)}"
            if stderr:
                message = f"{message}. {stderr}"
            if task:
                raise TaskValidationError(message, task.task_id, task.title)
            raise TaskValidationError(message, "unknown", "unknown")
        return result.stdout.decode("utf-8", errors="replace")

    def _git_raw(self, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.worktree_path)] + args,
            check=False,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "git_command_failed",
                args=" ".join(args),
                error=result.stderr.decode("utf-8", errors="replace").strip(),
            )
            return ""
        return result.stdout.decode("utf-8", errors="replace").strip()

    async def wait_for_done_marker(
        self,
        poll_interval_seconds: int,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        """Wait for ACE_TASK_DONE.json to appear and return its contents."""
        start = asyncio.get_event_loop().time()
        while True:
            marker = self._read_done_marker()
            if marker:
                return marker
            if timeout_seconds is not None and timeout_seconds > 0:
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed >= timeout_seconds:
                    return None
            await asyncio.sleep(poll_interval_seconds)


def _extract_json(text: str) -> str | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return None
