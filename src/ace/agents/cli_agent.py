"""CLI-based agent that runs in a tmux session."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.config.secrets import resolve_github_token
from ace.workspaces.tmux_ops import TmuxOps

from .base import AgentResult, AgentStatus, BaseAgent
from .policy import get_policy_prompt

logger = structlog.get_logger(__name__)


class CliAgent(BaseAgent):
    """Spawn a tmux session running a local CLI agent."""

    def __init__(self, backend: str, model: str | None = None):
        self.settings = get_settings()
        self.backend = backend.lower()
        self.model = model or self._default_model()
        self.tmux = TmuxOps()

    async def plan(self, task: str, context: dict[str, Any]) -> str:
        return "CLI agent runs in tmux; plan is included in the prompt file."

    async def run(
        self,
        task: str,
        context: dict[str, Any],
        workspace_path: str,
    ) -> AgentResult:
        workdir = Path(workspace_path)
        workdir.mkdir(parents=True, exist_ok=True)

        prompt_file = workdir / "ACE_TASK.md"
        if prompt_file.exists():
            prompt = prompt_file.read_text(encoding="utf-8")
        else:
            prompt = self._build_prompt(task, context, workspace_path)
            try:
                prompt_file.write_text(prompt, encoding="utf-8")
            except Exception as e:
                logger.warning("prompt_write_failed", error=str(e), path=str(prompt_file))

        try:
            command, command_display, template_has_prompt = self._build_command(prompt)
            session_name = self._session_name(context)
            token = resolve_github_token(self.settings)
            env = {}
            if token:
                env[self.settings.github_mcp_token_env] = token
                if self.settings.github_mcp_token_env != "GITHUB_CONTROL_API_KEY":
                    env["GITHUB_CONTROL_API_KEY"] = token

            created = self.tmux.start_session(session_name, workdir, command, env=env)
            if created and not template_has_prompt:
                condensed_prompt = self._condense_prompt(prompt)
                self.tmux.send_prompt(session_name, condensed_prompt)

            if created:
                output = (
                    f"Spawned tmux session '{session_name}' with {self.backend} "
                    f"model '{self.model}'."
                )
            else:
                output = f"Tmux session '{session_name}' already running."
            return AgentResult(
                status=AgentStatus.SUCCESS,
                output=output,
                files_changed=[],
                commands_run=[command_display] if created else [],
                metadata={
                    "session_name": session_name,
                    "worktree": str(workdir),
                    "prompt_file": str(prompt_file),
                    "backend": self.backend,
                    "model": self.model,
                    "created": created,
                },
            )
        except Exception as e:
            logger.error("cli_agent_spawn_failed", error=str(e))
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error=str(e),
            )

    async def respond_to_answer(
        self,
        answer: str,
        previous_result: AgentResult,
        workspace_path: str,
    ) -> AgentResult:
        session_name = (previous_result.metadata or {}).get("session_name")
        if session_name:
            self.tmux.send_prompt(session_name, self._condense_prompt(answer))
            return AgentResult(
                status=AgentStatus.SUCCESS,
                output=f"Sent answer to tmux session '{session_name}'.",
                files_changed=[],
                commands_run=[],
            )

        return AgentResult(
            status=AgentStatus.FAILED,
            output="",
            files_changed=[],
            commands_run=[],
            error="No tmux session found in previous result metadata.",
        )

    def _default_model(self) -> str:
        if self.backend == "claude":
            return self.settings.claude_model
        return self.settings.codex_model

    def _command_template(self) -> str:
        if self.backend == "claude":
            return self.settings.claude_cli_command
        return self.settings.codex_cli_command

    def _build_command(self, prompt: str) -> tuple[list[str], str, bool]:
        template = self._command_template()
        template_has_prompt = "{prompt}" in template

        model_value = self.model or ""
        display = template.replace("{model}", model_value).replace("{prompt}", "<prompt>")

        formatted = template.replace("{model}", model_value).replace("{prompt}", prompt)
        command = shlex.split(formatted)

        if not template_has_prompt and model_value and "--model" not in command:
            command += ["--model", model_value]
            display += f" --model {model_value}"

        return command, display, template_has_prompt

    def _build_prompt(self, task: str, context: dict[str, Any], workspace_path: str) -> str:
        policy = get_policy_prompt()
        repo_name = context.get("repo_name", "unknown")
        repo_owner = context.get("repo_owner", "unknown")
        issue_number = context.get("issue_number", "unknown")
        agent_label = self.settings.github_agent_label
        blocked_assignee = self.settings.blocked_assignee
        task_id = context.get("task_id", "task-1")
        branch_name = context.get("branch_name", "agent-branch")

        return (
            f"{policy}\n\n"
            "You are a coding agent working on a GitHub issue.\n\n"
            f"Task:\n{task}\n\n"
            "Context:\n"
            f"- Repository: {repo_owner}/{repo_name}\n"
            f"- Issue: #{issue_number}\n"
            f"- Workspace: {workspace_path}\n"
            f"- Branch: {branch_name}\n"
            "\n"
            "## GitHub MCP Access\n"
            "GitHub MCP is configured for this session. Use it for issue comments/metadata as needed.\n\n"
            "## CLI Blocked Protocol (No Questions in Session)\n"
            "If you need clarification or approval:\n"
            "1. Do NOT ask questions in this tmux session.\n"
            "2. Post a GitHub comment with your questions (prefix with BLOCKED).\n"
            f"3. Assign the issue to {blocked_assignee} and remove the '{agent_label}' label.\n"
            "4. Exit the session after posting the comment.\n\n"
            "Preferred (gh CLI):\n"
            f"  gh issue comment {issue_number} -R {repo_owner}/{repo_name} "
            f"-b \"**BLOCKED - Agent Needs Input**\\n\\n1. <question>\"\n"
            f"  gh issue edit {issue_number} -R {repo_owner}/{repo_name} "
            f"--add-assignee {blocked_assignee} --remove-label {agent_label}\n\n"
            "Fallback (curl with $GITHUB_CONTROL_API_KEY):\n"
            f"  curl -s -X POST -H \"Authorization: token $GITHUB_CONTROL_API_KEY\" "
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}/comments "
            f"-d '{{\"body\":\"**BLOCKED - Agent Needs Input**\\\\n\\\\n1. <question>\"}}'\n"
            f"  curl -s -X POST -H \"Authorization: token $GITHUB_CONTROL_API_KEY\" "
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}/assignees "
            f"-d '{{\"assignees\":[\"{blocked_assignee}\"]}}'\n"
            f"  curl -s -X DELETE -H \"Authorization: token $GITHUB_CONTROL_API_KEY\" "
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}/labels/{agent_label}\n"
            "\n"
            "## Completion Protocol\n"
            "When finished:\n"
            f"1. Commit changes on `{branch_name}` with a message that includes the task name.\n"
            f"2. Push the branch: `git push origin {branch_name}`.\n"
            "3. Write a JSON file named `ACE_TASK_DONE.json` in the repo root:\n\n"
            "```json\n"
            "{\n"
            f"  \"task_id\": \"{task_id}\",\n"
            "  \"summary\": \"<summary>\",\n"
            "  \"files_changed\": [\"...\"],\n"
            "  \"commands_run\": [\"...\"]\n"
            "}\n"
            "```\n"
            "Do NOT open a PR; the manager will open it after all tasks are complete.\n"
        )

    def _session_name(self, context: dict[str, Any]) -> str:
        repo = context.get("repo_name", "repo")
        issue = context.get("issue_number", "issue")
        raw = f"ace-{repo}-{issue}"
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")
        return slug[:60] if len(slug) > 60 else slug

    def _condense_prompt(self, prompt: str) -> str:
        return " ".join(prompt.split())
