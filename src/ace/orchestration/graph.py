"""LangGraph orchestration graph definition (issue-level, no task planning)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
import time
from typing import Any
from urllib.parse import quote

import structlog
from langgraph.graph import StateGraph

from ace.agents.base import AgentResult, AgentStatus
from ace.agents.llm_client import call_openai
from ace.config.secrets import resolve_openai_api_key
from ace.agents.model_selector import ModelSelector
from ace.config.secrets import resolve_github_token
from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.github.status_manager import StatusManager
from ace.metrics import metrics
from ace.orchestration.state import WorkerState
from ace.workspaces.git_ops import GitOps
from ace.workspaces.tmux_ops import TmuxOps

logger = structlog.get_logger(__name__)


class InstructionBuilder:
    """Creates detailed instructions for the entire issue."""

    def __init__(self, backend: str, model: str | None = None) -> None:
        self.backend = backend.lower()
        self.settings = get_settings()
        self._openai_key = resolve_openai_api_key(self.settings)
        self.instruction_backend = "openai"
        self.instruction_model = self.settings.instruction_model or self.settings.codex_model

    async def build(self, issue) -> str:
        prompt = self._build_prompt(issue)
        instructions = await self._call_model(
            prompt,
            trace_name="issue_instructions",
            metadata={
                "issue_number": issue.number,
                "issue_title": issue.title,
            },
        )
        cleaned = (instructions or "").strip()
        if (
            not cleaned
            or "type': 'reasoning" in cleaned
            or cleaned.startswith("{'id':")
        ):
            preview = cleaned[:400]
            logger.error("instruction_generation_failed", preview=preview)
            raise ValueError(
                "Instruction agent returned no usable instructions. "
                f"Preview: {preview}"
            )
        refusal_markers = [
            "i'm sorry",
            "i am sorry",
            "i cannot help",
            "i can't help",
            "cannot assist",
            "can't assist",
        ]
        lower_cleaned = cleaned.lower()
        if any(marker in lower_cleaned for marker in refusal_markers):
            preview = cleaned[:400]
            logger.error("instruction_generation_refused", preview=preview)
            raise ValueError(
                "Instruction agent refused to provide steps. "
                f"Preview: {preview}"
            )
        return instructions

    def _build_prompt(self, issue) -> str:
        body = f"""
You are an instruction agent. Write detailed, step-by-step *programmatic* coding instructions
for the issue below. Output Markdown only. Do not include UI/manual steps.
Before writing steps, check if AGENTS.md exists in the repo root and follow any practices listed there.
Do not refuse or apologize; if information is missing, make reasonable assumptions and proceed with best-effort coding steps.
If schema changes are needed, generate a timestamped Supabase migration (e.g., supabase/migrations/<YYYYMMDDHHMMSS>__desc.sql) using the current system time; do not place schema DDL in docs.

Issue Title: {issue.title}
Issue Body:
{issue.body}

Include:
- Key files/areas to inspect
- Concrete steps to implement
- Validation/tests to run
"""
        completion = """

When finished:
- Create a file ACE_TASK_DONE.json in the repo root with fields: task_id (use "task-1"), summary, files_changed (array), commands_run (array).
- Exit the session only after writing this file.
"""
        return body + completion

    async def _call_model(
        self,
        prompt: str,
        *,
        trace_name: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        return await call_openai(
            prompt,
            self.instruction_model,
            self._openai_key,
            max_tokens=1200,
            trace_name=trace_name,
            metadata=metadata,
        )

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
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

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
    """Execute the agent once for the full issue (no task planning)."""
    logger.info("step_run_agent", issue=state.issue_number, backend=state.backend)
    state.current_step = "run_agent"

    if not state.issue:
        logger.error("no_issue_to_process", issue=state.issue_number)
        state.last_update = datetime.now()
        return state

    settings = get_settings()
    github_token = resolve_github_token(settings)
    context = {
        "repo_name": state.metadata.get("repo_name", "unknown"),
        "repo_owner": state.metadata.get("repo_owner", "unknown"),
        "issue_number": state.issue_number,
        "labels": state.issue.labels,
    }

    repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
    repo_name = state.metadata.get("repo_name") or state.issue.repo_name
    if not repo_owner or not repo_name:
        raise ValueError("missing repo owner/name for worktree creation")

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
    context["repo_name"] = repo_name
    context["repo_owner"] = repo_owner
    context["branch_name"] = branch_name
    context["workspace_path"] = workspace_path

    instruction_builder = InstructionBuilder(backend=state.backend)
    instructions = await instruction_builder.build(state.issue)
    instructions_path = Path(worktree_path) / "ACE_TASK.md"
    instructions_path.write_text(instructions, encoding="utf-8")
    context["instructions_path"] = str(instructions_path)
    logger.info(
        "instructions_generated",
        issue=state.issue_number,
        path=str(instructions_path),
        preview=instructions[:400],
    )

    if settings.agent_execution_mode.lower() in {"tmux", "cli"}:
        from ace.agents.cli_agent import CliAgent

        model = state.metadata.get("model")
        agent = CliAgent(backend=state.backend, model=model)
    else:
        if state.backend == "claude":
            from ace.agents.claude_agent import ClaudeAgent

            model = state.metadata.get("model")
            agent = ClaudeAgent(model=model)
        else:
            from ace.agents.codex_agent import CodexAgent

            agent = CodexAgent()

    logger.info(
        "executing_agent",
        issue=state.issue_number,
        backend=state.backend,
        model=state.metadata.get("model"),
    )

    result = await agent.run(instructions, context, workspace_path)
    state.agent_result = result

    session_name = None
    if result.metadata and isinstance(result.metadata, dict):
        session_name = result.metadata.get("session_name")
    if session_name:
        logger.info(
            "tmux_session_ready",
            session=session_name,
            attach=f"tmux attach -t {session_name}",
        )

    # If running in tmux/cli, wait for ACE_TASK_DONE.json to appear before success.
    if settings.agent_execution_mode.lower() in {"tmux", "cli"} and session_name:
        tmux = TmuxOps()
        timeout = settings.task_wait_timeout_seconds if settings.task_wait_timeout_seconds > 0 else None
        done_path = Path(workspace_path) / "ACE_TASK_DONE.json"
        start = time.monotonic()
        while True:
            if done_path.exists():
                try:
                    marker = json.loads(done_path.read_text(encoding="utf-8"))
                    summary = marker.get("summary", "")
                except Exception:
                    summary = ""
                state.agent_result = AgentResult(
                    status=AgentStatus.SUCCESS,
                    output=summary or "Completed via CLI (ACE_TASK_DONE.json found).",
                    files_changed=[],
                    commands_run=[],
                )
                break
            if not tmux.session_exists(session_name):
                state.agent_result = AgentResult(
                    status=AgentStatus.FAILED,
                    output="tmux session ended without ACE_TASK_DONE.json.",
                    files_changed=[],
                    commands_run=[],
                    error="missing_done_file",
                )
                state.error = state.agent_result.error
                break
            if timeout is not None and (time.monotonic() - start) > timeout:
                state.agent_result = AgentResult(
                    status=AgentStatus.FAILED,
                    output="Timed out waiting for ACE_TASK_DONE.json.",
                    files_changed=[],
                    commands_run=[],
                    error="task_wait_timeout",
                )
                state.error = state.agent_result.error
                break
            time.sleep(5)

    logger.info(
        "agent_execution_complete",
        issue=state.issue_number,
        status=result.status.value,
        output_length=len(result.output),
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
    if state.error and not (state.agent_result and state.agent_result.status == AgentStatus.SUCCESS):
        state.agent_result = AgentResult(
            status=AgentStatus.FAILED,
            output=state.error,
            files_changed=[],
            commands_run=[],
            error=state.error,
        )
    state.last_update = datetime.now()
    return state


async def handle_blocked(state: WorkerState) -> WorkerState:
    logger.info("step_handle_blocked", issue=state.issue_number)
    state.current_step = "handle_blocked"

    if state.agent_result and state.agent_result.blocked_questions:
        try:
            settings = get_settings()
            api_client = _get_api_client(settings)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
            repo_name = state.metadata.get("repo_name") or state.issue.repo_name
            await status_manager.mark_blocked(
                state.issue_number,
                state.agent_result.blocked_questions,
                assignee="kristinday",
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
        except Exception as e:
            logger.error("mark_blocked_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def open_pr(state: WorkerState) -> WorkerState:
    logger.info("step_open_pr", issue=state.issue_number)
    state.current_step = "open_pr"

    if state.issue and state.agent_result and state.agent_result.status == AgentStatus.SUCCESS:
        try:
            settings = get_settings()
            github_token = resolve_github_token(settings)
            api_client = GitHubAPIClient(github_token)
            issue_queue = IssueQueue(api_client, state.issue.repo_owner, state.issue.repo_name)

            pr_title = f"Agent: {state.issue.title}"
            pr_body = f"Closes #{state.issue.number}\n\nWork completed by agent."
            head = f"{state.issue.repo_owner}:{state.branch_name}"
            pr = await issue_queue.create_pull_request(
                title=pr_title,
                body=pr_body,
                head=head,
                base=settings.github_base_branch,
                repo_owner=state.issue.repo_owner,
                repo_name=state.issue.repo_name,
            )
            state.pr_number = pr.get("number")
            state.pr_url = pr.get("html_url")
        except Exception as e:
            logger.error("pr_creation_failed", issue=state.issue_number, error=str(e))
            state.error = state.error or str(e)

    state.last_update = datetime.now()
    return state


async def post_failure(state: WorkerState) -> WorkerState:
    logger.info("step_post_failure", issue=state.issue_number, error=state.error)
    state.current_step = "post_failure"

    if state.issue_number and state.error:
        try:
            settings = get_settings()
            api_client = _get_api_client(settings)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
            repo_name = state.metadata.get("repo_name") or state.issue.repo_name
            await status_manager.mark_failed(
                state.issue_number,
                state.error,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
        except Exception as e:
            logger.error("mark_failed_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def mark_done(state: WorkerState) -> WorkerState:
    logger.info("step_mark_done", issue=state.issue_number, pr=state.pr_number)
    state.current_step = "mark_done"

    if state.issue_number and state.pr_number and state.pr_url:
        try:
            settings = get_settings()
            api_client = _get_api_client(settings)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            repo_owner = state.metadata.get("repo_owner") or state.issue.repo_owner
            repo_name = state.metadata.get("repo_name") or state.issue.repo_name
            await status_manager.mark_done(
                state.issue_number,
                state.pr_number,
                state.pr_url,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
        except Exception as e:
            logger.error("mark_done_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


def create_workflow_graph() -> StateGraph:
    workflow = StateGraph(WorkerState)

    workflow.add_node("fetch_candidates", fetch_candidates)
    workflow.add_node("claim_issue", claim_issue)
    workflow.add_node("hydrate_context", hydrate_context)
    workflow.add_node("select_backend", select_backend)
    workflow.add_node("run_agent", run_agent)
    workflow.add_node("evaluate_result", evaluate_result)
    workflow.add_node("handle_blocked", handle_blocked)
    workflow.add_node("open_pr", open_pr)
    workflow.add_node("post_failure", post_failure)
    workflow.add_node("mark_done", mark_done)

    workflow.set_entry_point("fetch_candidates")

    workflow.add_edge("fetch_candidates", "claim_issue")
    workflow.add_edge("claim_issue", "hydrate_context")
    workflow.add_edge("hydrate_context", "select_backend")
    workflow.add_edge("select_backend", "run_agent")
    workflow.add_edge("run_agent", "evaluate_result")

    def route_after_evaluate(state: WorkerState) -> str:
        if state.agent_result and state.agent_result.blocked_questions:
            return "handle_blocked"
        elif state.agent_result and state.agent_result.status.value == "success" and not state.error:
            return "open_pr"
        else:
            return "post_failure"

    workflow.add_conditional_edges(
        "evaluate_result",
        route_after_evaluate,
        {
            "handle_blocked": "handle_blocked",
            "open_pr": "open_pr",
            "post_failure": "post_failure",
        },
    )

    workflow.add_edge("handle_blocked", "mark_done")
    workflow.add_edge("open_pr", "mark_done")
    workflow.add_edge("post_failure", "mark_done")

    workflow.set_finish_point("mark_done")
    return workflow


def get_compiled_graph():
    workflow = create_workflow_graph()
    return workflow.compile()
