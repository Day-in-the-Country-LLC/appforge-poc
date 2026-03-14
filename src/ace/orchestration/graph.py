"""LangGraph orchestration graph definition (issue-level, no task subdivision)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import structlog
from langgraph.graph import StateGraph

from ace.agents.model_selector import ModelSelector
from ace.agents.types import AgentResult, AgentStatus
from ace.config.secrets import resolve_github_token
from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.status_manager import StatusManager
from ace.issue_tracking import build_work_item_enricher, build_work_item_tracker
from ace.logging_utils import log_key_event
from ace.webhooks.lifecycle import (
    STAGE_SESSION_RESUME,
    STAGE_SESSION_START,
    STAGE_SESSION_STALL,
    STAGE_SESSION_TURN_COMPLETE,
    STAGE_SESSION_TURN_START,
    build_session_lifecycle_context,
    log_session_lifecycle_event,
)
from ace.notifications.slack_client import SlackNotifier, format_completion_message
from ace.orchestration.session_runtime import (
    SessionContext,
    SessionInstructionBuilder,
    SessionTurnType,
    build_session_runner,
    done_filename_for_run,
    normalize_session_run_id,
)
from ace.orchestration.state import WorkerState
from ace.workspaces.git_ops import GitOps
from ace.workspaces.tmux_ops import TmuxOps

logger = structlog.get_logger(__name__)


def _slugify_title(title: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        return "issue"
    return slug[:max_length]


def _build_repo_url(owner: str, repo: str, token: str) -> str:
    if token:
        safe_token = quote(token, safe="")
        return f"https://x-access-token:{safe_token}@github.com/{owner}/{repo}.git"
    return f"https://github.com/{owner}/{repo}.git"


def _get_api_client(settings) -> GitHubAPIClient:
    token = resolve_github_token(settings)
    return GitHubAPIClient(token)


# Workflow steps
async def fetch_candidates(state: WorkerState) -> WorkerState:
    logger.info("step_fetch_candidates")
    state.current_step = "fetch_candidates"
    state.last_update = datetime.now()
    return state


async def claim_issue(state: WorkerState) -> WorkerState:
    logger.info("step_claim_issue", issue=state.issue_number)
    state.current_step = "claim_issue"

    if state.issue and state.branch_name:
        try:
            settings = get_settings()
            api_client = _get_api_client(settings)
            tracker = build_work_item_tracker(
                api_client,
                settings.github_org,
                "",
            )
            enricher = build_work_item_enricher(
                api_client,
                settings.github_org,
                "",
            )
            status_manager = StatusManager(tracker=tracker, enricher=enricher)
            repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
            repo_name = state.metadata.get("repo_name") or state.issue.repo_name
            await status_manager.claim_issue(
                state.issue_number,
                repo_owner,
                repo_name,
                state.branch_name,
            )
        except Exception as e:
            logger.error("claim_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def hydrate_context(state: WorkerState) -> WorkerState:
    logger.info("step_hydrate_context", issue=state.issue_number)
    state.current_step = "hydrate_context"
    state.last_update = datetime.now()
    return state


async def select_backend(state: WorkerState) -> WorkerState:
    logger.info("step_select_backend", issue=state.issue_number)
    state.current_step = "select_backend"

    if not state.issue:
        logger.warning("no_issue_in_state", issue=state.issue_number)
        state.backend = "codex"
        state.metadata["model"] = "gpt-5.1-codex"
    else:
        selector = ModelSelector()
        try:
            model_config = selector.select_model(state.issue.labels)
            state.backend = model_config.backend
            state.metadata["model"] = model_config.model
            logger.info(
                "backend_selected",
                issue=state.issue_number,
                backend=state.backend,
                model=model_config.model,
            )
        except ValueError as e:
            logger.warning("difficulty_selection_failed", issue=state.issue_number, error=str(e))
            default_config = selector.get_default_model()
            state.backend = default_config.backend
            state.metadata["model"] = default_config.model

    state.last_update = datetime.now()
    return state


async def run_agent(state: WorkerState) -> WorkerState:
    """Execute the agent once for the full issue (no task subdivision)."""
    logger.info("step_run_agent", issue=state.issue_number, backend=state.backend)
    state.current_step = "run_agent"

    if not state.issue:
        logger.error("no_issue_to_process", issue=state.issue_number)
        state.last_update = datetime.now()
        return state

    settings = get_settings()
    github_token = resolve_github_token(settings)
    run_context: dict[str, Any] = {
        "repo_name": state.metadata.get("repo_name", "unknown"),
        "repo_owner": state.metadata.get("repo_owner", "unknown"),
        "issue_number": state.issue_number,
        "labels": state.issue.labels,
        "source": state.metadata.get("source", "worker"),
    }

    repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
    repo_name = state.metadata.get("repo_name") or state.issue.repo_name
    if not repo_owner or not repo_name:
        raise ValueError("missing repo owner/name for worktree creation")

    project_session_key = state.metadata.get("project_session_key") or state.metadata.get("project_slug")
    if hasattr(GitOps, "from_settings"):
        git_ops = GitOps.from_settings(settings, project_session_key=project_session_key)
    else:  # pragma: no cover - compatibility fallback
        git_ops = GitOps(settings.agent_workspace_root)
    worktree_path = git_ops.get_worktree_path(repo_name, state.issue_number)
    if not worktree_path.exists():
        repo_url = _build_repo_url(repo_owner, repo_name, github_token)
        await git_ops.clone_repo(repo_url, repo_name, state.issue_number)

    branch_slug = _slugify_title(state.issue.title)
    branch_name = git_ops.get_branch_name(state.issue_number, branch_slug)
    await git_ops.ensure_branch(worktree_path, branch_name)

    state.workspace_path = str(worktree_path)
    state.branch_name = branch_name
    workspace_path = state.workspace_path
    run_context["repo_name"] = repo_name
    run_context["repo_owner"] = repo_owner
    run_context["branch_name"] = branch_name
    run_context["workspace_path"] = workspace_path

    agents_md = ""
    agents_path = worktree_path / "AGENTS.md"
    if agents_path.exists():
        try:
            agents_md = agents_path.read_text(encoding="utf-8").strip()
        except Exception:
            agents_md = ""

    run_id_seed = f"issue-{state.issue_number}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    state.session_id = normalize_session_run_id(state.session_id or run_id_seed)
    done_filename = done_filename_for_run(state.session_id)
    state.metadata["run_id"] = state.session_id
    state.metadata["done_filename"] = done_filename
    state.metadata["session_turn"] = state.session_turn
    state.metadata["retry_count"] = state.retry_count
    run_context["run_id"] = state.session_id
    run_context["done_filename"] = done_filename
    run_context["session_turn"] = state.session_turn
    run_context["retry_count"] = state.retry_count
    run_context["workflow_id"] = state.metadata.get("workflow_id", state.session_id)
    session_workflow_id = run_context["workflow_id"]
    state.metadata["source"] = run_context["source"]
    session_issue_key = f"{state.issue.repo_owner}/{state.issue.repo_name}#{state.issue.number}"
    session_lifecycle_context = build_session_lifecycle_context(
        session_id=state.session_id,
        turn_number=state.session_turn,
        workflow_id=session_workflow_id,
        source=run_context["source"],
        issue_key=session_issue_key,
        project=run_context["repo_name"],
    )
    log_session_lifecycle_event(logger, STAGE_SESSION_START, session_lifecycle_context)
    if state.previous_output:
        log_session_lifecycle_event(
            logger,
            STAGE_SESSION_RESUME,
            session_lifecycle_context,
            reason="previous_output_detected",
        )
    log_session_lifecycle_event(
        logger,
        STAGE_SESSION_TURN_START,
        session_lifecycle_context,
        labels=state.issue.labels,
    )

    previous_output = state.previous_output or None
    if state.retry_count:
        turn_type = SessionTurnType.RETRY
    elif state.session_turn > 1:
        turn_type = SessionTurnType.CONTINUATION
    else:
        turn_type = None

    session_context = SessionContext(
        issue_number=state.issue_number,
        issue_title=state.issue.title,
        issue_body=state.issue.body,
        repo_owner=repo_owner,
        repo_name=repo_name,
        run_id=state.session_id,
        workspace_path=workspace_path,
        branch_name=branch_name,
        done_filename=done_filename,
        backend=state.backend,
        model=state.metadata.get("model"),
        turn_type=turn_type,
        turn_number=state.session_turn,
        previous_output=previous_output,
        metadata=run_context,
    )

    instruction_builder = SessionInstructionBuilder()
    instructions = await instruction_builder.build(
        state.issue,
        agents_md=agents_md or None,
        context=session_context,
    )
    instructions_path = Path(worktree_path) / "ACE_TASK.md"
    instructions_path.write_text(instructions, encoding="utf-8")
    run_context["instructions_path"] = str(instructions_path)
    logger.info(
        "instructions_generated",
        issue=state.issue_number,
        path=str(instructions_path),
        preview=instructions[:400],
    )
    log_key_event(
        logger,
        "🧭 Instructions created",
        issue=state.issue_number,
        path=str(instructions_path),
    )

    if settings.agent_execution_mode.lower() not in {"tmux", "cli"}:
        raise ValueError("❌ ERROR: Non-tmux execution modes are not supported. Use tmux/cli.")

    runner = build_session_runner(state.backend, model=state.metadata.get("model"))

    previous_result = None
    if state.session_turn > 1 and previous_output:
        previous_result = AgentResult(
            status=AgentStatus.FAILED,
            output=previous_output,
            files_changed=[],
            commands_run=[],
            error=previous_output,
        )

    logger.info(
        "executing_agent",
        issue=state.issue_number,
        backend=state.backend,
        model=state.metadata.get("model"),
        run_id=state.session_id,
        run_turn=state.session_turn,
        done_filename=done_filename,
    )

    await runner.start(session_context)
    result = await runner.run_turn(session_context, instructions, previous_result=previous_result)
    state.agent_result = result

    requested_input = False
    if result.status == AgentStatus.FAILED:
        state.error = result.error or result.output or "agent_failed"
        if await runner.request_input(result):
            requested_input = True
            log_session_lifecycle_event(
                logger,
                STAGE_SESSION_STALL,
                session_lifecycle_context,
                reason="runner_request_input",
                error=state.error,
            )
            state.previous_output = state.previous_output or result.output or result.error or ""
            state.session_turn += 1
            state.retry_count += 1
        if result.error == "task_wait_timeout":
            await runner.timeout(session_context, reason="task_wait_timeout")
        else:
            await runner.stop(session_context, reason=state.error)
    else:
        await runner.stop(session_context, reason="completed")

    log_session_lifecycle_event(
        logger,
        STAGE_SESSION_TURN_COMPLETE,
        session_lifecycle_context,
        turn_result=result.status.value,
        requested_input=requested_input,
        output_length=len(result.output),
        error=state.error,
    )
    logger.info(
        "agent_execution_complete",
        issue=state.issue_number,
        status=result.status.value,
        output_length=len(result.output),
        session_id=session_context.run_id,
        done_filename=done_filename,
    )

    state.last_update = datetime.now()
    return state


async def evaluate_result(state: WorkerState) -> WorkerState:
    logger.info("step_evaluate_result", issue=state.issue_number)
    state.current_step = "evaluate_result"
    if state.agent_result and state.agent_result.status != AgentStatus.SUCCESS:
        if not state.error:
            state.error = state.agent_result.error or state.agent_result.output or "agent_failed"
    # If any prior step set an error (e.g., PR creation), treat as failure.
    if state.error:
        log_key_event(
            logger,
            f"❌ ERROR: {state.error}",
            issue=state.issue_number,
        )
        state.agent_result = AgentResult(
            status=AgentStatus.FAILED,
            output=state.error,
            files_changed=[],
            commands_run=[],
            error=state.error,
        )
    state.last_update = datetime.now()
    return state


async def manager_cleanup(state: WorkerState) -> WorkerState:
    logger.info("step_manager_cleanup", issue=state.issue_number)
    state.current_step = "manager_cleanup"

    workdir = Path(state.workspace_path) if state.workspace_path else None
    done_filename = (
        str(state.metadata.get("done_filename"))
        if isinstance(state.metadata, dict)
        else None
    )
    done_filename = done_filename if done_filename and done_filename.strip() else "ACE_TASK_DONE.json"
    done_path = workdir / done_filename if workdir else None
    if done_path and not done_path.exists() and done_path.name != "ACE_TASK_DONE.json":
        legacy_done_path = workdir / "ACE_TASK_DONE.json"
        if legacy_done_path.exists():
            done_path = legacy_done_path
    task_path = workdir / "ACE_TASK.md" if workdir else None

    status = "unknown"
    summary = None
    blocked_questions: list[str] | None = None
    if done_path and done_path.exists():
        marker: dict[str, Any] = {}
        try:
            marker = json.loads(done_path.read_text(encoding="utf-8"))
        except Exception:
            marker = {}
        summary = marker.get("summary")
        raw_status = str(marker.get("status") or marker.get("state") or "").lower()
        blocked_value = marker.get("blocked")
        marker_questions = marker.get("blocked_questions")
        if isinstance(marker_questions, list):
            blocked_questions = [str(item) for item in marker_questions if str(item).strip()]
        if raw_status == "blocked" or blocked_value is True:
            status = "blocked"
        elif blocked_questions:
            status = "blocked"
        else:
            status = "completed"
    elif done_path:
        status = "missing_done_file"

    if state.agent_result and state.agent_result.status == AgentStatus.FAILED:
        status = "failed"

    await _notify_slack_completion(
        state=state,
        status=status,
        summary=summary,
        blocked_questions=blocked_questions,
    )

    log_key_event(
        logger,
        f"🧹 MANAGER CLEANUP: {status}",
        issue=state.issue_number,
        status=status,
        workdir=str(workdir) if workdir else None,
    )

    session_name = None
    if state.agent_result and isinstance(state.agent_result.metadata, dict):
        session_name = state.agent_result.metadata.get("session_name")
    if session_name:
        try:
            TmuxOps().kill_session(session_name)
        except Exception as exc:
            logger.warning(
                "manager_cleanup_tmux_kill_failed",
                issue=state.issue_number,
                session=session_name,
                error=str(exc),
            )

    for path, label in ((done_path, "ACE_TASK_DONE.json"), (task_path, "ACE_TASK.md")):
        if path and path.exists():
            try:
                path.unlink()
                logger.info(
                    "manager_cleanup_deleted_file",
                    issue=state.issue_number,
                    path=str(path),
                    label=label,
                )
            except Exception as exc:
                logger.warning(
                    "manager_cleanup_delete_failed",
                    issue=state.issue_number,
                    path=str(path),
                    label=label,
                    error=str(exc),
                )

    state.last_update = datetime.now()
    return state


async def _notify_slack_completion(
    *,
    state: WorkerState,
    status: str,
    summary: str | None,
    blocked_questions: list[str] | None,
) -> None:
    settings = get_settings()
    try:
        notifier = SlackNotifier.from_settings(settings)
    except ValueError as exc:
        logger.error("slack_notifier_config_failed", error=f"❌ ERROR: {exc}")
        return
    if notifier is None:
        return

    repo = None
    if state.issue and state.issue.repo_owner and state.issue.repo_name:
        repo = f"{state.issue.repo_owner}/{state.issue.repo_name}"
    else:
        owner = state.metadata.get("repo_owner") if isinstance(state.metadata, dict) else None
        name = state.metadata.get("repo_name") if isinstance(state.metadata, dict) else None
        if owner and name:
            repo = f"{owner}/{name}"

    error = state.error
    output = None
    if state.agent_result:
        output = state.agent_result.output
        if not error:
            error = state.agent_result.error or None

    message = format_completion_message(
        status=status,
        issue_number=state.issue_number,
        repo=repo,
        summary=summary or output,
        blocked_questions=blocked_questions,
        error=error,
        output=output,
        pr_url=state.pr_url,
    )
    if message is None:
        return
    # Avoid duplicate Slack noise when the CLI agent already posted a spawn-failure alert.
    if (
        status == "failed"
        and state.agent_result
        and isinstance(state.agent_result.metadata, dict)
        and state.agent_result.metadata.get("slack_notification_type") == "cli_spawn_failed"
        and state.agent_result.metadata.get("slack_notified") is True
    ):
        return
    await notifier.safe_post(message)


def create_workflow_graph() -> StateGraph:
    workflow = StateGraph(WorkerState)

    workflow.add_node("fetch_candidates", fetch_candidates)
    workflow.add_node("claim_issue", claim_issue)
    workflow.add_node("hydrate_context", hydrate_context)
    workflow.add_node("select_backend", select_backend)
    workflow.add_node("run_agent", run_agent)
    workflow.add_node("evaluate_result", evaluate_result)
    workflow.add_node("manager_cleanup", manager_cleanup)

    workflow.set_entry_point("fetch_candidates")

    workflow.add_edge("fetch_candidates", "claim_issue")
    workflow.add_edge("claim_issue", "hydrate_context")
    workflow.add_edge("hydrate_context", "select_backend")
    workflow.add_edge("select_backend", "run_agent")
    workflow.add_edge("run_agent", "evaluate_result")
    workflow.add_edge("evaluate_result", "manager_cleanup")

    workflow.set_finish_point("manager_cleanup")
    return workflow


def get_compiled_graph():
    workflow = create_workflow_graph()
    return workflow.compile()
