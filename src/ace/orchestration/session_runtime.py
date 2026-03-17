"""Session execution abstractions and session-aware instruction generation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

import structlog

from ace.agents.cli_agent import CliAgent
from ace.agents.llm_client import call_claude, call_openai
from ace.agents.types import AgentResult, AgentStatus
from ace.config.secrets import resolve_claude_api_key, resolve_openai_api_key
from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)


def normalize_session_run_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        value = f"run-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    if not sanitized:
        raise ValueError("❌ ERROR: invalid run_id after sanitization")
    return sanitized


def done_filename_for_run(run_id: str) -> str:
    return f"ACE_TASK_DONE.{run_id}.json"


class SessionTurnType(str, Enum):
    """Supported session turn semantics."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    RETRY = "retry"
    RESUME = "resume"


@dataclass(frozen=True)
class SessionContext:
    """Context for one execution session turn."""

    issue_number: int
    issue_title: str
    issue_body: str
    repo_owner: str
    repo_name: str
    run_id: str
    workspace_path: str
    branch_name: str
    done_filename: str
    backend: str = "codex"
    model: str | None = None
    turn_type: SessionTurnType | None = None
    turn_number: int = 1
    previous_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionRunner(Protocol):
    """Backend-agnostic runtime execution for session turns."""

    async def start(self, context: SessionContext) -> str:
        """Start a session and return a session id."""

    async def run_turn(
        self,
        context: SessionContext,
        instructions: str,
        *,
        previous_result: AgentResult | None = None,
    ) -> AgentResult:
        """Run one prompt turn for a session."""

    async def continue_turn(
        self,
        context: SessionContext,
        instructions: str,
        *,
        previous_result: AgentResult | None = None,
    ) -> AgentResult:
        """Continue a live session with the next prompt turn."""

    async def request_input(self, result: AgentResult) -> bool:
        """Return True when the result indicates the runtime needs user input."""

    async def stop(self, context: SessionContext, *, reason: str | None = None) -> None:
        """Stop a session."""

    async def timeout(self, context: SessionContext, *, reason: str | None = None) -> None:
        """Handle a timeout event for a session."""


class CliBatchRunner:
    """One-shot runner that preserves existing CLI behavior."""

    def __init__(self, backend: str, model: str | None = None):
        self.backend = backend.lower()
        self.model = model
        self._agent = CliAgent(backend=self.backend, model=model)

    async def start(self, context: SessionContext) -> str:
        _ = context
        return f"cli-batch:{context.run_id}"

    async def run_turn(
        self,
        context: SessionContext,
        instructions: str,
        *,
        previous_result: AgentResult | None = None,
    ) -> AgentResult:
        return await self.continue_turn(context, instructions, previous_result=previous_result)

    async def continue_turn(
        self,
        context: SessionContext,
        instructions: str,
        *,
        previous_result: AgentResult | None = None,
    ) -> AgentResult:
        if context.turn_number > 1 and previous_result is not None:
            return await self._agent.respond_to_answer(
                str(previous_result.error or previous_result.output or ""),
                previous_result=previous_result,
                workspace_path=context.workspace_path,
            )

        run_context = {
            "repo_name": context.repo_name,
            "repo_owner": context.repo_owner,
            "issue_number": context.issue_number,
            "branch_name": context.branch_name,
            "run_id": context.run_id,
            "done_filename": context.done_filename,
        }
        run_context.update(context.metadata)
        return await self._agent.run(instructions, run_context, context.workspace_path)

    async def request_input(self, result: AgentResult) -> bool:
        if result.status != AgentStatus.FAILED:
            return False
        if not result.error:
            return False
        normalized = result.error.lower()
        markers = {
            "no interactive session available",
            "requires input",
            "input required",
        }
        return any(marker in normalized for marker in markers)

    async def stop(self, context: SessionContext, *, reason: str | None = None) -> None:
        logger.info("session_runner_stop", session_id=context.run_id, reason=reason)
        return None

    async def timeout(self, context: SessionContext, *, reason: str | None = None) -> None:
        logger.warning("session_runner_timeout", session_id=context.run_id, reason=reason)
        return None


class CodexBatchRunner(CliBatchRunner):
    """Batch runner for Codex-compatible backend."""

    def __init__(self, model: str | None = None):
        super().__init__(backend="codex", model=model)


class ClaudeBatchRunner(CliBatchRunner):
    """Batch runner for Claude backend."""

    def __init__(self, model: str | None = None):
        super().__init__(backend="claude", model=model)


class CodexSessionRunner(CliBatchRunner):
    """Persistent-mode session runner for Codex-compatible backend."""

    def __init__(self, model: str | None = None):
        super().__init__(backend="codex", model=model)


class ClaudeSessionRunner(CliBatchRunner):
    """Persistent-mode session runner for Claude backend."""

    def __init__(self, model: str | None = None):
        super().__init__(backend="claude", model=model)


def _normalize_session_mode(session_mode: str | None) -> str:
    normalized = (session_mode or "").strip().lower()
    if not normalized:
        normalized = (getattr(get_settings(), "agent_session_mode", "") or "").strip().lower()
    if not normalized:
        normalized = "batch"
    if normalized not in {"batch", "persistent"}:
        source = normalized
        raise ValueError(f"❌ ERROR: unsupported session mode: {source}")
    return normalized


def _build_batch_runner(backend: str, *, model: str | None = None) -> SessionRunner:
    runner_by_backend = {
        "codex": CodexBatchRunner,
        "claude": ClaudeBatchRunner,
    }
    factory = runner_by_backend.get(backend)
    if factory is None:
        raise ValueError(f"❌ ERROR: unsupported backend: {backend}")
    return factory(model=model)


def _build_persistent_runner(backend: str, *, model: str | None = None) -> SessionRunner:
    runner_by_backend = {
        "codex": CodexSessionRunner,
        "claude": ClaudeSessionRunner,
    }
    factory = runner_by_backend.get(backend)
    if factory is None:
        raise ValueError(f"❌ ERROR: unsupported backend: {backend}")
    return factory(model=model)


def build_session_runner(
    backend: str,
    *,
    model: str | None = None,
    session_mode: str | None = None,
) -> SessionRunner:
    normalized_backend = backend.lower()
    if normalized_backend not in {"codex", "claude"}:
        raise ValueError(f"❌ ERROR: unsupported backend: {backend}")

    mode = _normalize_session_mode(session_mode)
    if mode == "persistent":
        return _build_persistent_runner(normalized_backend, model=model)
    return _build_batch_runner(normalized_backend, model=model)


class SessionInstructionBuilder:
    """Builds session-aware instructions for an issue."""

    def __init__(self) -> None:
        self._inner_builder = _LegacyInstructionBuilder()

    async def build(
        self,
        issue,
        *,
        agents_md: str | None = None,
        context: SessionContext,
    ) -> str:
        turn_type = self._resolve_turn_type(context.turn_type, context.turn_number)
        if turn_type == SessionTurnType.INITIAL:
            return await self._inner_builder.build_initial(
                issue,
                agents_md=agents_md,
                done_filename=context.done_filename,
                task_id=context.run_id,
            )
        if turn_type == SessionTurnType.RETRY:
            return self._build_retry(
                issue,
                previous_output=context.previous_output,
                done_filename=context.done_filename,
                task_id=context.run_id,
            )
        if turn_type == SessionTurnType.RESUME:
            return self._build_resume(
                issue,
                previous_output=context.previous_output,
                done_filename=context.done_filename,
                task_id=context.run_id,
            )
        return self._build_continuation(
            issue,
            previous_output=context.previous_output,
            done_filename=context.done_filename,
            task_id=context.run_id,
        )

    def _resolve_turn_type(
        self,
        turn_type: SessionTurnType | str | None,
        turn_number: int,
    ) -> SessionTurnType:
        if turn_type is not None:
            if isinstance(turn_type, SessionTurnType):
                return turn_type
            normalized = str(turn_type).strip().lower()
            try:
                return SessionTurnType(normalized)
            except ValueError as exc:
                raise ValueError(
                    f"❌ ERROR: unsupported turn_type: {turn_type}"
                ) from exc
        return SessionTurnType.INITIAL if turn_number <= 1 else SessionTurnType.CONTINUATION

    def _build_continuation(
        self,
        issue,
        *,
        previous_output: str | None,
        done_filename: str,
        task_id: str,
    ) -> str:
        if not issue:
            raise ValueError("❌ ERROR: issue is required for continuation instructions")
        prev = (previous_output or "").strip()
        if not prev:
            prev = "No prior output was captured."
        return (
            f"Continue this issue after a previous interrupted attempt.\n\n"
            f"Issue Title: {issue.title}\n"
            f"Issue Body:\n{issue.body}\n\n"
            f"Previous Output:\n{prev}\n\n"
            f"Resume and finish the issue using the same repository and workspace.\n"
            f"Create {done_filename} with task_id={task_id} when complete."
        )

    def _build_retry(
        self,
        issue,
        *,
        previous_output: str | None,
        done_filename: str,
        task_id: str,
    ) -> str:
        if not issue:
            raise ValueError("❌ ERROR: issue is required for retry instructions")
        prev = (previous_output or "").strip()
        if not prev:
            prev = "No prior output was captured."
        return (
            f"Retry this issue after a failed run.\n\n"
            f"Issue Title: {issue.title}\n"
            f"Issue Body:\n{issue.body}\n\n"
            f"Previous Output:\n{prev}\n\n"
            f"Address the failure and continue from the same workspace context.\n"
            f"Create {done_filename} with task_id={task_id} when complete."
        )

    def _build_resume(
        self,
        issue,
        *,
        previous_output: str | None,
        done_filename: str,
        task_id: str,
    ) -> str:
        if not issue:
            raise ValueError("❌ ERROR: issue is required for resume instructions")
        prev = (previous_output or "").strip()
        if not prev:
            prev = "No prior output was captured."
        return (
            f"Resume this issue execution after a pause or restart.\n\n"
            f"Issue Title: {issue.title}\n"
            f"Issue Body:\n{issue.body}\n\n"
            f"Previous Output:\n{prev}\n\n"
            f"Continue from the same work context and finish this task.\n"
            f"Create {done_filename} with task_id={task_id} when complete."
        )


class _LegacyInstructionBuilder:
    """Legacy instruction generation extracted for compatibility while becoming session-aware."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.instruction_backend = self.settings.instruction_backend.lower()
        if self.instruction_backend == "claude":
            self._claude_key = resolve_claude_api_key(self.settings)
            self._openai_key = ""
            self.instruction_model = (
                self.settings.instruction_model or self.settings.claude_model
            )
        elif self.instruction_backend == "openai":
            self._openai_key = resolve_openai_api_key(self.settings)
            self._claude_key = ""
            self.instruction_model = self.settings.instruction_model or self.settings.codex_model
        else:
            self._openai_key = ""
            self._claude_key = ""
            self.instruction_model = self.settings.instruction_model or self.settings.codex_model

    async def build_initial(
        self,
        issue,
        *,
        agents_md: str | None = None,
        done_filename: str = "ACE_TASK_DONE.json",
        task_id: str = "task-1",
    ) -> str:
        prompt = self._build_prompt(
            issue,
            agents_md=agents_md,
            done_filename=done_filename,
            task_id=task_id,
        )
        instructions = await self._call_model(
            prompt,
            trace_name="issue_instructions",
            metadata={
                "issue_number": issue.number,
                "issue_title": issue.title,
            },
        )
        cleaned = (instructions or "").strip()
        if not cleaned or "type': 'reasoning" in cleaned or cleaned.startswith("{'id':"):
            preview = cleaned[:400]
            raise ValueError(
                f"❌ ERROR: Instruction agent returned no usable instructions. Preview: {preview}"
            )

        normalized = cleaned.lower()
        normalized = (
            normalized.replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
        )
        normalized = " ".join(normalized.split())

        refusal_markers = [
            "i'm sorry",
            "i am sorry",
            "i cannot help",
            "i can't help",
            "cannot assist",
            "can't assist",
            "can't help with that",
            "cannot help with that",
            "i can't help with that",
            "i cannot help with that",
            "i’m sorry",
        ]
        if any(marker in normalized for marker in refusal_markers):
            preview = cleaned[:400]
            raise ValueError(
                f"❌ ERROR: Instruction agent refused to provide steps. Preview: {preview}"
            )
        return instructions

    def _build_prompt(
        self,
        issue,
        *,
        agents_md: str | None = None,
        done_filename: str = "ACE_TASK_DONE.json",
        task_id: str = "task-1",
    ) -> str:
        agents_section = ""
        if agents_md:
            agents_section = f"\n\nAGENTS.md (follow these repo practices):\n{agents_md}\n"
        body = f"""
You are an instruction agent. Write detailed, step-by-step *programmatic* coding instructions
for the issue below. Output Markdown only. Do not include UI/manual steps.
Assume the repository is available; do not claim you cannot access files.
Do not refuse or apologize; if information is missing, make reasonable assumptions
and proceed with best-effort coding steps.
If schema changes are needed, generate a timestamped Supabase migration
(e.g., supabase/migrations/<YYYYMMDDHHMMSS>__desc.sql) using the current system time; do not place schema DDL in docs.

Issue Title: {issue.title}
Issue Body:
{issue.body}
{agents_section}

Include:
- Key files/areas to inspect
- Concrete steps to implement
- Validation/tests to run
"""
        completion = """

When finished:
- Create a file {done_filename} in the repo root with fields: task_id
  (use "{task_id}"), summary, files_changed (array), commands_run (array).
- Exit the session only after writing this file.
"""
        return body + completion.format(done_filename=done_filename, task_id=task_id)

    async def _call_model(
        self,
        prompt: str,
        *,
        trace_name: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        if self.instruction_backend == "openai":
            return await call_openai(
                prompt,
                self.instruction_model,
                self._openai_key,
                max_tokens=1200,
                trace_name=trace_name,
                metadata=metadata,
            )
        if self.instruction_backend == "claude":
            return await call_claude(
                prompt,
                self.instruction_model,
                self._claude_key,
                max_tokens=1200,
                trace_name=trace_name,
                metadata=metadata,
            )
        raise ValueError(
            f"❌ ERROR: Unsupported instruction backend: {self.instruction_backend}"
        )
