"""CLI-based agent that runs in a tmux session."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.config.secrets import resolve_github_token
from ace.workspaces.tmux_ops import TmuxOps, session_name_for_issue

from .base import AgentResult, AgentStatus, BaseAgent
from .mcp_config import ensure_mcp_config

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
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error="ACE_TASK.md missing; instructions must be generated before spawn.",
            )

        try:
            command, command_display, template_has_prompt = self._build_command(prompt)
            session_name = self._session_name(context)
            token = resolve_github_token(self.settings)
            env = {}
            if token:
                env[self.settings.github_mcp_token_env] = token
                if self.settings.github_mcp_token_env != "GITHUB_CONTROL_API_KEY":
                    env["GITHUB_CONTROL_API_KEY"] = token

            if token:
                ensure_mcp_config(workdir, self.backend, token, self.settings)

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

    def _session_name(self, context: dict[str, Any]) -> str:
        repo = context.get("repo_name", "repo")
        issue = context.get("issue_number", "issue")
        return session_name_for_issue(str(repo), issue)

    def _condense_prompt(self, prompt: str) -> str:
        return " ".join(prompt.split())
