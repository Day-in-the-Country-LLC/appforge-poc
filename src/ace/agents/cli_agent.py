"""CLI-based agent runner."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from ace.config.secrets import resolve_claude_api_key, resolve_github_token, resolve_openai_api_key
from ace.config.settings import get_settings
from ace.notifications.slack_client import SlackMessage, SlackNotifier

from .mcp_config import ensure_mcp_config
from .types import AgentResult, AgentStatus

logger = structlog.get_logger(__name__)


class CliAgent:
    """Run a local CLI agent non-interactively."""

    def __init__(self, backend: str, model: str | None = None):
        self.settings = get_settings()
        self.backend = backend.lower()
        self.model = model or self._default_model()

    async def run(
        self,
        task: str,
        context: dict[str, Any],
        workspace_path: str,
    ) -> AgentResult:
        workdir = Path(workspace_path)
        workdir.mkdir(parents=True, exist_ok=True)

        prompt_file = workdir / "ACE_TASK.md"
        if not prompt_file.exists():
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error="ACE_TASK.md missing; instructions must be generated before spawn.",
            )

        try:
            system_prompt = self._load_system_prompt()
            # Always pass an explicit default task prompt that instructs reading ACE_TASK.md.
            # This repo disallows missing-prompt execution and disallows fallbacks.
            base_prompt = self._load_task_prompt()
            if self.backend == "codex" and system_prompt:
                prompt_for_cli = f"{system_prompt}\n\n{base_prompt}"
            else:
                prompt_for_cli = base_prompt

            command, command_display = self._build_command(
                prompt_for_cli,
                system_prompt=system_prompt,
            )
            token = resolve_github_token(self.settings)
            env_exports: dict[str, str] = {}
            if token:
                env_exports["GITHUB_TOKEN"] = token

            if self.backend == "codex":
                openai_key = resolve_openai_api_key(self.settings)
                env_exports["OPENAI_API_KEY"] = openai_key

            try:
                claude_key = resolve_claude_api_key(self.settings)
                env_exports["ANTHROPIC_API_KEY"] = claude_key
            except Exception:
                # If backend is codex, we can skip Claude; otherwise propagate when invoked.
                if self.backend == "claude":
                    raise

            env_exports["DISABLE_LOGIN_COMMAND"] = "1"
            env_exports["FORCE_CODE_TERMINAL"] = "1"

            if token:
                ensure_mcp_config(workdir, self.backend, token, self.settings)

            self._ensure_claude_guide(workdir)

            done_path = workdir / "ACE_TASK_DONE.json"
            # Avoid stale completion markers from previous runs.
            if done_path.exists():
                done_path.unlink()

            timeout = self.settings.task_wait_timeout_seconds
            timeout_seconds = timeout if timeout > 0 else None
            return_code, stdout_text, stderr_text, terminated_on_done = self._run_cli_until_done(
                command=command,
                workdir=workdir,
                env={**os.environ, **env_exports},
                done_path=done_path,
                timeout_seconds=timeout_seconds,
            )

            marker = self._load_done_marker(done_path)

            summary = str(marker.get("summary") or "").strip()
            files_changed = (
                marker.get("files_changed") if isinstance(marker.get("files_changed"), list) else []
            )
            commands_run = (
                marker.get("commands_run") if isinstance(marker.get("commands_run"), list) else []
            )
            if not commands_run:
                commands_run = [command_display]

            normalized_summary = " ".join(summary.lower().split())
            refusal_markers = [
                "no actionable instructions",
                "refusal",
                "refused",
                "can't help",
                "cannot help",
                "i'm sorry",
                "i am sorry",
            ]
            if any(marker_text in normalized_summary for marker_text in refusal_markers):
                return AgentResult(
                    status=AgentStatus.FAILED,
                    output=summary or "Instruction refusal detected in ACE_TASK_DONE.json.",
                    files_changed=files_changed,
                    commands_run=commands_run,
                    error="instruction_refusal",
                )

            if return_code != 0 and not terminated_on_done:
                stderr = stderr_text.strip()
                stdout = stdout_text.strip()
                details = stderr or stdout or f"exit code {return_code}"
                raise RuntimeError(f"❌ ERROR: cli_process_failed: {details}")

            return AgentResult(
                status=AgentStatus.SUCCESS,
                output=summary or "Completed via CLI.",
                files_changed=files_changed,
                commands_run=commands_run,
                metadata={
                    "worktree": str(workdir),
                    "prompt_file": str(prompt_file),
                    "backend": self.backend,
                    "model": self.model,
                    "execution_mode": "subprocess",
                    "terminated_on_done_file": terminated_on_done,
                },
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "cli_agent_timeout",
                error=f"❌ ERROR: {exc}",
                workdir=str(workdir),
            )
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error="task_wait_timeout",
            )
        except Exception as e:
            logger.error(
                "cli_agent_spawn_failed",
                error=str(e),
                workdir=str(workdir) if "workdir" in locals() else None,
            )
            # Notify Slack immediately for CLI spawn failures (these can otherwise be easy to miss).
            slack_notified = False
            try:
                try:
                    notifier = SlackNotifier.from_settings(self.settings)
                except ValueError as exc:
                    logger.error("slack_notifier_config_failed", error=f"❌ ERROR: {exc}")
                    notifier = None
                if notifier is not None:
                    await notifier.safe_post(
                        SlackMessage(
                            text=(
                                "❌ ACE CLI agent spawn failed | "
                                f"backend={self.backend} | "
                                f"model={self.model} | "
                                f"workdir={workdir} | "
                                f"error={e}"
                            )
                        )
                    )
                    slack_notified = True
            except Exception as notify_exc:
                logger.error(
                    "slack_notification_failed",
                    error=f"❌ ERROR: {notify_exc}",
                )
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error=str(e),
                metadata={
                    "slack_notified": slack_notified,
                    "slack_notification_type": "cli_spawn_failed",
                },
            )

    async def respond_to_answer(
        self,
        answer: str,
        previous_result: AgentResult,
        workspace_path: str,
    ) -> AgentResult:
        return AgentResult(
            status=AgentStatus.FAILED,
            output="",
            files_changed=[],
            commands_run=[],
            error="No interactive session available in subprocess mode.",
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
    ) -> tuple[list[str], str]:
        template = self._command_template()

        if "{prompt}" not in template:
            raise RuntimeError(
                "❌ ERROR: invalid_cli_command_template: command must include {prompt}. "
                "This repo requires an explicit prompt on every CLI invocation."
            )

        model_value = self.model or ""
        display = template.replace("{model}", model_value).replace("{prompt}", "<prompt>")

        # Quote prompt so shell parsing keeps it as one positional argument.
        formatted = template.replace("{model}", model_value)
        if "{prompt}" in formatted:
            formatted = formatted.replace("{prompt}", shlex.quote(prompt))
        command = shlex.split(formatted)

        if self.backend == "claude" and system_prompt and "--append-system-prompt" not in command:
            command += ["--append-system-prompt", system_prompt]
            display += " --append-system-prompt <system_prompt>"

        if model_value and "--model" not in command:
            command += ["--model", model_value]
            display += f" --model {model_value}"

        return command, display

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
            text = path.read_text(encoding="utf-8").strip()
            # Keep prompt content single-line safe when passed through CLI args.
            return " ".join(text.split())
        except Exception as exc:
            logger.warning("system_prompt_read_failed", path=str(path), error=str(exc))
            return ""

    def _load_task_prompt(self) -> str:
        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "prompts" / "cli_task_prompt.md"
        try:
            if not path.exists():
                raise RuntimeError(
                    f"❌ ERROR: Required prompt file missing: {path}. "
                    "This repo requires an explicit default prompt (no fallbacks)."
                )
            text = path.read_text(encoding="utf-8").strip()
            normalized = " ".join(text.split())
            if not normalized:
                raise RuntimeError(
                    f"❌ ERROR: Required prompt file is empty: {path}. "
                    "This repo requires an explicit default prompt (no fallbacks)."
                )
            return normalized
        except Exception as exc:
            # Fail loudly; no fallbacks in this repo.
            raise RuntimeError(f"❌ ERROR: task_prompt_read_failed ({path}): {exc}") from exc

    def _run_cli_until_done(
        self,
        *,
        command: list[str],
        workdir: Path,
        env: dict[str, str],
        done_path: Path,
        timeout_seconds: int | None,
    ) -> tuple[int, str, str, bool]:
        started_at = time.monotonic()
        terminated_on_done = False

        with (
            tempfile.TemporaryFile(mode="w+b") as stdout_capture,
            tempfile.TemporaryFile(mode="w+b") as stderr_capture,
        ):
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                command,
                cwd=str(workdir),
                env=env,
                stdout=stdout_capture,
                stderr=stderr_capture,
            )
            try:
                while True:
                    if done_path.exists():
                        terminated_on_done = True
                        logger.info(
                            "done_file_detected_terminating_cli",
                            done_path=str(done_path),
                            workdir=str(workdir),
                            pid=proc.pid,
                        )
                        self._terminate_process(proc)
                        break

                    if proc.poll() is not None:
                        break

                    if (
                        timeout_seconds is not None
                        and (time.monotonic() - started_at) >= timeout_seconds
                    ):
                        self._terminate_process(proc)
                        raise subprocess.TimeoutExpired(command, timeout_seconds)

                    time.sleep(1.0)
            finally:
                if proc.poll() is None:
                    self._terminate_process(proc)

            stdout_capture.seek(0)
            stderr_capture.seek(0)
            stdout_text = stdout_capture.read().decode("utf-8", errors="replace")
            stderr_text = stderr_capture.read().decode("utf-8", errors="replace")

            return proc.returncode or 0, stdout_text, stderr_text, terminated_on_done

    def _terminate_process(self, proc: subprocess.Popen[bytes], grace_seconds: float = 5.0) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=grace_seconds)

    def _load_done_marker(self, done_path: Path) -> dict[str, Any]:
        if not done_path.exists():
            raise RuntimeError("❌ ERROR: missing_done_file: ACE_TASK_DONE.json was not produced.")

        last_error: Exception | None = None
        # The file can appear before write+flush is complete; retry briefly.
        for _ in range(10):
            try:
                return json.loads(done_path.read_text(encoding="utf-8"))
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)

        raise RuntimeError(f"❌ ERROR: invalid_done_file: {last_error}")

    def _ensure_claude_guide(self, workdir: Path) -> None:
        """Copy a shared CLAUDE.md into the workspace if one isn't present."""
        try:
            source = Path(self.settings.claude_guide_path).expanduser()
            if not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(_DEFAULT_CLAUDE_GUIDE.strip() + "\n", encoding="utf-8")

            dest = workdir / "CLAUDE.md"
            if dest.exists():
                return

            dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("claude_guide_injected", path=str(dest))
        except Exception as exc:
            logger.warning("claude_guide_inject_failed", workdir=str(workdir), error=str(exc))


_DEFAULT_CLAUDE_GUIDE = """
# CLAUDE.md

## How to work
- Read ACE_TASK.md fully before acting; follow steps in order.
- Prefer minimal changes; keep commits tight and focused when asked.
- Ask clarifying questions in the GitHub issue if requirements are unclear.
- When blocked by credentials or missing services, leave a concise issue comment.
- Always format code with repo standards; run available linters/tests when practical.

## Tooling
- You are running non-interactively via subprocess. Keep output concise.
- MCP servers: GitHub (official) and Appforge MCP for project board filtering.
- GitHub token is injected as GITHUB_TOKEN.

## Delivery
- Summarize changes at the end (what, why, tests).
- If no changes made, state that explicitly with the reason.
"""
