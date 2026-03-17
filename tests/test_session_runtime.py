"""Tests for orchestration session runtime abstractions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ace.agents.types import AgentResult, AgentStatus
from ace.orchestration import session_runtime


def _issue_stub() -> SimpleNamespace:
    return SimpleNamespace(number=123, title="Test Issue", body="Issue body details")


def _session_context(**overrides) -> session_runtime.SessionContext:
    defaults = dict(
        issue_number=123,
        issue_title="Test Issue",
        issue_body="Issue body details",
        repo_owner="owner",
        repo_name="repo",
        run_id="run-20260101010101-abcdef12",
        workspace_path="/tmp/project",
        branch_name="issue-123-test",
        done_filename="ACE_TASK_DONE.run-20260101010101-abcdef12.json",
        backend="codex",
        model="gpt-5.1-codex",
        turn_number=1,
        previous_output=None,
        metadata={"repo_name": "repo"},
    )
    defaults.update(overrides)
    return session_runtime.SessionContext(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_session_instruction_builder_generates_continuation_text(monkeypatch) -> None:
    class _FakeLegacyInstructionBuilder:
        async def build_initial(self, issue, *, agents_md=None, done_filename="ACE_TASK_DONE.json", task_id="task-1"):  # noqa: ARG001
            del issue, agents_md, done_filename, task_id
            return "fallback"

    monkeypatch.setattr(session_runtime, "_LegacyInstructionBuilder", _FakeLegacyInstructionBuilder)

    builder = session_runtime.SessionInstructionBuilder()
    result = await builder.build(
        issue=_issue_stub(),
        context=_session_context(turn_number=3, previous_output="Prior failure"),
    )

    assert result.startswith("Continue this issue after a previous interrupted attempt.")
    assert "Previous Output:\nPrior failure" in result


def test_normalize_session_run_id_sanitizes() -> None:
    run_id = session_runtime.normalize_session_run_id("  run/with spaces&&bad$chars  ")
    assert " " not in run_id
    assert ".." not in run_id
    assert run_id == "run-with-spaces-bad-chars"


def test_normalize_session_run_id_generates_default() -> None:
    run_id = session_runtime.normalize_session_run_id("")

    assert run_id.startswith("run-")
    assert len(run_id) > 5
    assert run_id.count("-") == 2


def test_done_filename_for_run() -> None:
    assert (
        session_runtime.done_filename_for_run("run-20260101000000-12345678")
        == "ACE_TASK_DONE.run-20260101000000-12345678.json"
    )


@pytest.mark.asyncio
async def test_session_instruction_builder_generates_turn_specific_prompts(monkeypatch) -> None:
    class _FakeLegacyInstructionBuilder:
        async def build_initial(
            self,
            issue,
            *,
            agents_md=None,  # noqa: ARG002
            done_filename: str = "ACE_TASK_DONE.json",
            task_id: str = "task-1",
        ) -> str:
            del issue, agents_md
            return f"initial {done_filename} {task_id}"

    monkeypatch.setattr(session_runtime, "_LegacyInstructionBuilder", _FakeLegacyInstructionBuilder)

    builder = session_runtime.SessionInstructionBuilder()

    issue = _issue_stub()

    context = _session_context(turn_type=session_runtime.SessionTurnType.RETRY)
    retry_prompt = await builder.build(issue, context=context)
    assert "Retry this issue after a failed run." in retry_prompt
    assert "Previous Output:\nNo prior output was captured." in retry_prompt

    resume_prompt = await builder.build(
        issue,
        context=_session_context(turn_type="resume"),
    )
    assert "Resume this issue execution after a pause or restart." in resume_prompt

    continuation_prompt = await builder.build(
        issue,
        context=_session_context(
            turn_type="continuation",
            previous_output="Prior failure",
        ),
    )
    assert (
        continuation_prompt
        == "Continue this issue after a previous interrupted attempt.\n\n"
        "Issue Title: Test Issue\n"
        "Issue Body:\nIssue body details\n\n"
        "Previous Output:\nPrior failure\n\n"
        "Resume and finish the issue using the same repository and workspace.\n"
        "Create ACE_TASK_DONE.run-20260101010101-abcdef12.json with task_id=run-20260101010101-abcdef12 when complete."
    )

    with pytest.raises(ValueError, match="unsupported turn_type"):
        await builder.build(issue, context=_session_context(turn_type="invalid"))


@pytest.mark.asyncio
async def test_cli_batch_runner_start_and_run_turn(monkeypatch, tmp_path: Path) -> None:
    run_inputs: list[tuple[str, str, str]] = []
    run_outputs: list[tuple[str, str, str]] = []

    class FakeCliAgent:
        def __init__(self, backend: str, model: str | None = None) -> None:
            self.backend = backend
            self.model = model

        async def run(
            self,
            instructions: str,
            context: dict[str, object],
            workspace_path: str,
        ) -> AgentResult:
            run_inputs.append((instructions, str(context["run_id"]), workspace_path))
            return AgentResult(
                status=AgentStatus.SUCCESS,
                output="ok",
                files_changed=["a.py"],
                commands_run=["echo done"],
            )

        async def respond_to_answer(
            self,
            answer: str,
            previous_result: AgentResult,
            workspace_path: str,
        ) -> AgentResult:
            run_outputs.append((answer, previous_result.output, workspace_path))
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                error=answer,
            )

    monkeypatch.setattr(session_runtime, "CliAgent", FakeCliAgent)

    runner = session_runtime.CliBatchRunner("codex", model="gpt-5.2-codex")
    context = _session_context(workspace_path=str(tmp_path))

    session_id = await runner.start(context)
    assert session_id == f"cli-batch:{context.run_id}"

    result = await runner.run_turn(context, "execute this issue")
    assert result.status == AgentStatus.SUCCESS
    assert run_inputs == [("execute this issue", context.run_id, str(tmp_path))]

    continuation_result = await runner.run_turn(
        _session_context(turn_number=2, workspace_path=str(tmp_path)),
        "ignore",
        previous_result=AgentResult(status=AgentStatus.SUCCESS, output="previous done"),
    )
    assert continuation_result.status == AgentStatus.FAILED
    assert continuation_result.error == "previous done"
    assert run_outputs == [("previous done", "previous done", str(tmp_path))]

    continue_result = await runner.continue_turn(
        _session_context(turn_number=2, workspace_path=str(tmp_path)),
        "continue",
        previous_result=AgentResult(status=AgentStatus.SUCCESS, output="previous done again"),
    )
    assert continue_result.error == "previous done again"
    assert run_outputs[-1] == ("previous done again", "previous done again", str(tmp_path))


@pytest.mark.parametrize(
    ("backend",),
    [("codex",), ("claude",)],
)
def test_build_session_runner_returns_cli_batch_runner_for_supported_backend(backend: str) -> None:
    runner = session_runtime.build_session_runner(backend)
    if backend == "codex":
        assert isinstance(runner, session_runtime.CodexBatchRunner)
    else:
        assert isinstance(runner, session_runtime.ClaudeBatchRunner)


@pytest.mark.parametrize(
    ("backend", "mode", "expected_type"),
    [
        ("codex", "persistent", session_runtime.CodexSessionRunner),
        ("claude", "persistent", session_runtime.ClaudeSessionRunner),
    ],
)
def test_build_session_runner_routes_persistent_backend_when_session_mode_is_set(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    mode: str,
    expected_type: type[object],
) -> None:
    class _Settings:
        agent_session_mode = mode

    monkeypatch.setattr(session_runtime, "get_settings", lambda: _Settings())
    runner = session_runtime.build_session_runner(backend)
    assert isinstance(runner, expected_type)


def test_build_session_runner_respects_explicit_session_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Settings:
        agent_session_mode = "persistent"

    monkeypatch.setattr(session_runtime, "get_settings", lambda: _Settings())
    runner = session_runtime.build_session_runner("codex", session_mode="batch")
    assert isinstance(runner, session_runtime.CodexBatchRunner)


def test_build_session_runner_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unsupported backend"):
        session_runtime.build_session_runner("unknown")


def test_build_session_runner_rejects_unknown_session_mode() -> None:
    with pytest.raises(ValueError, match="unsupported session mode"):
        session_runtime.build_session_runner("codex", session_mode="unsupported")
