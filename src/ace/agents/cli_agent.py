"""CLI-based agent that runs in a tmux session."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.config.secrets import resolve_github_token, resolve_openai_api_key, resolve_claude_api_key
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
            system_prompt = self._load_system_prompt()
            prompt_for_cli = prompt
            if system_prompt and self.backend == "codex":
                prompt_for_cli = f"{system_prompt}\n\n{prompt}"

            command, command_display, template_has_prompt = self._build_command(
                prompt_for_cli,
                system_prompt=system_prompt,
            )
            # Always send the prompt after starting codex; ignore template prompt embedding.
            template_has_prompt = False
            session_name = self._session_name(context)
            token = resolve_github_token(self.settings)
            env_exports: dict[str, str] = {}
            if token:
                env_exports[self.settings.github_mcp_token_env] = token
                env_exports["GITHUB_CONTROL_API_KEY"] = token
                env_exports["GITHUB_TOKEN"] = token

            if self.backend == "codex":
                openai_key = resolve_openai_api_key(self.settings)
                env_exports["OPENAI_API_KEY"] = openai_key

            try:
                claude_key = resolve_claude_api_key(self.settings)
                env_exports["CLAUDE_CODE_ADMIN_API_KEY"] = claude_key
            except Exception:
                # If backend is codex, we can skip Claude; otherwise propagate when invoked.
                if self.backend == "claude":
                    raise

            gcp_credentials = context.get("gcp_credentials_path") or self.settings.gcp_credentials_path
            if gcp_credentials:
                env_exports["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_credentials
                env_exports["GCP_CREDENTIALS_FILE"] = gcp_credentials

            if token:
                ensure_mcp_config(workdir, self.backend, token, self.settings)

            # Start bare session, then send exports + exec codex in bash -lc with desired flags
            created = self.tmux.start_session(session_name, workdir, [], env=None)

            export_parts = [f"export {k}={shlex.quote(v)}" for k, v in env_exports.items()]
            exec_cmd = shlex.join(command)
            launch_cmd = "bash -lc " + shlex.quote("; ".join(export_parts + [f"exec {exec_cmd}"]))
            self.tmux.send_prompt(session_name, launch_cmd, delay_seconds=0.2)

            if self.backend == "claude":
                # Auto-accept bypass warning when running with dangerously-skip-permissions.
                self.tmux.send_prompt(session_name, "2", delay_seconds=0.6)

            if not template_has_prompt:
                if self.backend == "claude":
                    prompt_to_send = "Please read ACE_TASK.md in the current directory and execute all instructions end-to-end. When finished, summarize the changes and status."
                else:
                    prompt_to_send = self._condense_prompt(prompt_for_cli)
                self.tmux.send_prompt(session_name, prompt_to_send, delay_seconds=0.8)

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

    def _build_command(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
    ) -> tuple[list[str], str, bool]:
        template = self._command_template()
        template_has_prompt = "{prompt}" in template

        model_value = self.model or ""
        display = template.replace("{model}", model_value).replace("{prompt}", "<prompt>")

        formatted = template.replace("{model}", model_value).replace("{prompt}", prompt)
        command = shlex.split(formatted)

        if self.backend == "claude" and system_prompt and "--system-prompt" not in command:
            command += ["--system-prompt", system_prompt]
            display += " --system-prompt <system_prompt>"

        if not template_has_prompt and model_value and "--model" not in command:
            command += ["--model", model_value]
            display += f" --model {model_value}"

        return command, display, template_has_prompt

    def _load_system_prompt(self) -> str:
        path_value = self.settings.cli_system_prompt_path
        if not path_value:
            return ""
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            repo_root = Path(__file__).resolve().parents[3]
            path = repo_root / path
        try:
            if not path.exists():
                return ""
            return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("system_prompt_read_failed", path=str(path), error=str(exc))
            return ""

    def _session_name(self, context: dict[str, Any]) -> str:
        repo = context.get("repo_name", "repo")
        issue = context.get("issue_number", "issue")
        return session_name_for_issue(str(repo), issue)

    def _condense_prompt(self, prompt: str) -> str:
        return " ".join(prompt.split())
