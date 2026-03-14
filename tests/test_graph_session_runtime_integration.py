"""Tests for graph execution routed through session runtime abstractions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ace.agents.types import AgentResult, AgentStatus
from ace.orchestration import graph
from ace.orchestration.session_runtime import SessionTurnType
from ace.orchestration.state import WorkerState


class _BoundLogger:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = events
        self._bound = {}

    def bind(self, **kwargs):
        child = _BoundLogger(self._events)
        child._bound = {**self._bound, **kwargs}
        return child

    def info(self, event_name: str, **fields: object) -> None:
        merged = {**self._bound}
        merged.update(fields)
        self._events.append({"event_name": event_name, "fields": merged})


def _build_issue() -> SimpleNamespace:
    return SimpleNamespace(
        number=123,
        title="Session Runtime Issue",
        body="A tiny issue body.",
        labels=[],
        repo_owner="acme",
        repo_name="widget",
    )


class _DummyGitOps:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def get_worktree_path(self, repo_name: str, issue_number: int) -> Path:
        return self.root / "worktrees" / repo_name / str(issue_number)

    def get_branch_name(self, issue_number: int, slug: str) -> str:
        return f"agent/{issue_number}-{slug}"

    async def clone_repo(
        self,
        repo_url: str,  # noqa: ARG002
        repo_name: str,  # noqa: ARG002
        issue_number: int,  # noqa: ARG002
    ) -> Path:
        path = self.get_worktree_path(repo_name, issue_number)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def ensure_branch(
        self,
        worktree_path: Path,
        branch_name: str,
        base_branch: str | None = None,  # noqa: ARG002
    ) -> None:
        del base_branch
        worktree_path.mkdir(parents=True, exist_ok=True)


class _TrackingSettings:
    def __init__(self, workspace_root: str) -> None:
        self.agent_workspace_root = workspace_root
        self.agent_execution_mode = "cli"


class _BuilderProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def build(
        self,
        issue: SimpleNamespace,  # noqa: ARG002
        *,
        agents_md: str | None = None,  # noqa: ARG002
        context: object | None = None,
    ) -> str:
        del issue, agents_md
        self.calls.append({"context": context})
        return "build me some code"


class _RunnerProbe:
    def __init__(self, backend: str, model: str | None = None) -> None:
        self.backend = backend
        self.model = model
        self.start_contexts: list[object] = []
        self.run_calls: list[tuple[object, str, AgentResult | None]] = []
        self.stop_calls: list[tuple[object, str | None]] = []
        self.timeout_calls: list[tuple[object, str | None]] = []
        self.request_input_result = False

    async def start(self, context: object) -> str:
        self.start_contexts.append(context)
        return f"session-{context.run_id}"

    async def run_turn(
        self,
        context: object,
        instructions: str,
        *,
        previous_result: AgentResult | None = None,
    ) -> AgentResult:
        self.run_calls.append((context, instructions, previous_result))
        if self.request_input_result:
            return AgentResult(
                status=AgentStatus.FAILED,
                output="Input needed to continue.",
                files_changed=[],
                commands_run=[],
                error="input required",
            )
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output="completed",
            files_changed=[],
            commands_run=[],
        )

    async def request_input(self, result: AgentResult) -> bool:
        del result
        return self.request_input_result

    async def stop(self, context: object, *, reason: str | None = None) -> None:
        self.stop_calls.append((context, reason))

    async def timeout(self, context: object, *, reason: str | None = None) -> None:
        self.timeout_calls.append((context, reason))


@pytest.mark.asyncio
async def test_run_agent_uses_session_runner_and_dynamic_done_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_builder = _BuilderProbe()
    captured_runner = _RunnerProbe(backend="codex")

    def _build_runner(backend: str, model: str | None = None) -> _RunnerProbe:
        captured_runner.backend = backend
        captured_runner.model = model
        return captured_runner

    monkeypatch.setattr(graph, "GitOps", _DummyGitOps)
    monkeypatch.setattr(graph, "get_settings", lambda: _TrackingSettings(str(tmp_path)))
    monkeypatch.setattr(graph, "resolve_github_token", lambda _settings: "token")
    monkeypatch.setattr(graph, "SessionInstructionBuilder", lambda: captured_builder)
    monkeypatch.setattr(graph, "build_session_runner", _build_runner)

    state = WorkerState(issue=_build_issue(), issue_number=123, agent_id="agent-1")
    state.metadata["repo_owner"] = "acme"
    state.metadata["repo_name"] = "widget"

    result_state = await graph.run_agent(state)

    assert result_state.agent_result is not None
    assert result_state.agent_result.status == AgentStatus.SUCCESS
    assert result_state.agent_result.output == "completed"

    assert captured_builder.calls
    context = captured_builder.calls[0]["context"]
    assert context.run_id == result_state.metadata["run_id"]
    assert context.done_filename == result_state.metadata["done_filename"]
    assert result_state.metadata["done_filename"].startswith("ACE_TASK_DONE.")
    assert "run_id" in context.metadata
    assert "done_filename" in context.metadata
    assert result_state.workspace_path
    assert Path(result_state.workspace_path).exists()

    assert captured_runner.start_contexts
    assert captured_runner.run_calls
    assert captured_runner.stop_calls
    assert captured_runner.backend == "codex"


@pytest.mark.asyncio
async def test_run_agent_uses_retry_turn_type_for_restarts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_builder = _BuilderProbe()
    captured_runner = _RunnerProbe(backend="codex")
    captured_runner.request_input_result = True

    def _build_runner(backend: str, model: str | None = None) -> _RunnerProbe:
        captured_runner.backend = backend
        captured_runner.model = model
        return captured_runner

    monkeypatch.setattr(graph, "GitOps", _DummyGitOps)
    monkeypatch.setattr(graph, "get_settings", lambda: _TrackingSettings(str(tmp_path)))
    monkeypatch.setattr(graph, "resolve_github_token", lambda _settings: "token")
    monkeypatch.setattr(graph, "SessionInstructionBuilder", lambda: captured_builder)
    monkeypatch.setattr(graph, "build_session_runner", _build_runner)

    state = WorkerState(
        issue=_build_issue(),
        issue_number=123,
        agent_id="agent-1",
        session_turn=2,
        retry_count=1,
        previous_output="Need to continue from prior output.",
    )
    state.metadata["repo_owner"] = "acme"
    state.metadata["repo_name"] = "widget"

    result_state = await graph.run_agent(state)

    assert result_state.agent_result is not None
    assert result_state.agent_result.status == AgentStatus.FAILED
    assert result_state.retry_count == 2
    assert result_state.session_turn == 3
    assert result_state.previous_output == "Need to continue from prior output."
    assert captured_builder.calls
    context = captured_builder.calls[0]["context"]
    assert context.turn_type == SessionTurnType.RETRY
    _, _, previous_result = captured_runner.run_calls[0]
    assert previous_result is not None
    assert previous_result.error == "Need to continue from prior output."


@pytest.mark.asyncio
async def test_run_agent_emits_session_lifecycle_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_builder = _BuilderProbe()
    captured_runner = _RunnerProbe(backend="codex")

    events: list[dict[str, object]] = []
    logger = _BoundLogger(events)
    old_logger = graph.logger
    graph.logger = logger

    def _build_runner(backend: str, model: str | None = None) -> _RunnerProbe:
        captured_runner.backend = backend
        captured_runner.model = model
        return captured_runner

    try:
        monkeypatch.setattr(graph, "GitOps", _DummyGitOps)
        monkeypatch.setattr(graph, "get_settings", lambda: _TrackingSettings(str(tmp_path)))
        monkeypatch.setattr(graph, "resolve_github_token", lambda _settings: "token")
        monkeypatch.setattr(graph, "SessionInstructionBuilder", lambda: captured_builder)
        monkeypatch.setattr(graph, "build_session_runner", _build_runner)

        state = WorkerState(issue=_build_issue(), issue_number=123, agent_id="agent-1")
        state.metadata["repo_owner"] = "acme"
        state.metadata["repo_name"] = "widget"
        state.metadata["source"] = "github"

        result_state = await graph.run_agent(state)

        assert result_state.agent_result is not None
        session_events = [
            event for event in events if event["event_name"] == "session_lifecycle"
        ]
        stages = [event["fields"]["stage"] for event in session_events]
        assert stages[:3] == [
            "session_start",
            "session_turn_start",
            "session_turn_complete",
        ]
        first = session_events[0]["fields"]
        assert first["source"] == "github"
        assert first["issue_key"] == "acme/widget#123"
        assert first["turn_number"] == 1
    finally:
        graph.logger = old_logger


@pytest.mark.asyncio
async def test_run_agent_emits_session_resume_and_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_builder = _BuilderProbe()
    captured_runner = _RunnerProbe(backend="codex")
    captured_runner.request_input_result = True

    events: list[dict[str, object]] = []
    logger = _BoundLogger(events)
    old_logger = graph.logger
    graph.logger = logger

    def _build_runner(backend: str, model: str | None = None) -> _RunnerProbe:
        captured_runner.backend = backend
        captured_runner.model = model
        return captured_runner

    try:
        monkeypatch.setattr(graph, "GitOps", _DummyGitOps)
        monkeypatch.setattr(graph, "get_settings", lambda: _TrackingSettings(str(tmp_path)))
        monkeypatch.setattr(graph, "resolve_github_token", lambda _settings: "token")
        monkeypatch.setattr(graph, "SessionInstructionBuilder", lambda: captured_builder)
        monkeypatch.setattr(graph, "build_session_runner", _build_runner)

        state = WorkerState(
            issue=_build_issue(),
            issue_number=123,
            agent_id="agent-1",
            session_turn=2,
            retry_count=1,
            previous_output="Need to continue from prior output.",
        )
        state.metadata["repo_owner"] = "acme"
        state.metadata["repo_name"] = "widget"
        state.metadata["source"] = "linear"

        result_state = await graph.run_agent(state)

        assert result_state.agent_result is not None
        assert result_state.agent_result.status == AgentStatus.FAILED
        session_events = [
            event for event in events if event["event_name"] == "session_lifecycle"
        ]
        stages = [event["fields"]["stage"] for event in session_events]
        assert stages[:4] == [
            "session_start",
            "session_resume",
            "session_turn_start",
            "session_stall",
        ]
        assert stages[-1] == "session_turn_complete"
        assert session_events[1]["fields"]["reason"] == "previous_output_detected"
        assert session_events[-1]["fields"]["requested_input"] is True
    finally:
        graph.logger = old_logger
