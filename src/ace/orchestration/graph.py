"""LangGraph orchestration graph definition (issue-level, no task planning)."""

from __future__ import annotations

import base64
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
from ace.logging_utils import log_key_event
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

    async def build(
        self,
        issue,
        *,
        agents_md: str | None = None,
        work_type: str = "ready",
        pr_context: str | None = None,
    ) -> str:
        prompt = self._build_prompt(
            issue,
            agents_md=agents_md,
            work_type=work_type,
            pr_context=pr_context,
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
        if (
            not cleaned
            or "type': 'reasoning" in cleaned
            or cleaned.startswith("{'id':")
        ):
            preview = cleaned[:400]
            logger.error(
                "instruction_generation_failed",
                issue_number=issue.number,
                issue_title=issue.title,
                issue_body=issue.body or "",
                prompt=prompt,
                response=instructions or "",
                preview=preview,
            )
            raise ValueError(
                "❌ ERROR: Instruction agent returned no usable instructions. "
                f"Preview: {preview}"
            )

        normalized = cleaned.lower()
        normalized = normalized.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
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
            logger.error(
                "instruction_generation_refused",
                issue_number=issue.number,
                issue_title=issue.title,
                issue_body=issue.body or "",
                prompt=prompt,
                response=instructions or "",
                preview=preview,
            )
            raise ValueError(
                "❌ ERROR: Instruction agent refused to provide steps. "
                f"Preview: {preview}"
            )
        return instructions

    def _build_prompt(
        self,
        issue,
        *,
        agents_md: str | None = None,
        work_type: str = "ready",
        pr_context: str | None = None,
    ) -> str:
        agents_section = ""
        if agents_md:
            agents_section = f"\n\nAGENTS.md (follow these repo practices):\n{agents_md}\n"
        pr_section = ""
        if work_type == "pr_comment" and pr_context:
            pr_section = (
                "\n\nPR COMMENT CONTEXT (use this to draft fixes):\n"
                f"{pr_context}\n"
                "\nFollow the `claude-cli-pr-comment-update` skill.\n"
            )
        body = f"""
You are an instruction agent. Write detailed, step-by-step *programmatic* coding instructions
for the issue below. Output Markdown only. Do not include UI/manual steps.
Assume the repository is available; do not claim you cannot access files.
Do not refuse or apologize; if information is missing, make reasonable assumptions and proceed with best-effort coding steps.
If schema changes are needed, generate a timestamped Supabase migration (e.g., supabase/migrations/<YYYYMMDDHHMMSS>__desc.sql) using the current system time; do not place schema DDL in docs.

Issue Title: {issue.title}
Issue Body:
{issue.body}
{agents_section}{pr_section}

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


async def _collect_pr_comment_context(
    settings,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    *,
    comment_body: str,
    comment_path: str,
    comment_line: int | None,
    context_lines: int = 100,
) -> str:
    api_client = _get_api_client(settings)
    head_sha = None
    try:
        pr = await api_client.rest_get(f"/repos/{repo_owner}/{repo_name}/pulls/{pr_number}")
        head_sha = (pr.get("head") or {}).get("sha")
    except Exception as exc:
        logger.warning("pr_head_fetch_failed", issue=pr_number, error=str(exc))

    file_snippet = ""
    if comment_path and head_sha and comment_line:
        try:
            content = await api_client.rest_get(
                f"/repos/{repo_owner}/{repo_name}/contents/{comment_path}",
                params={"ref": head_sha},
            )
            encoded = content.get("content", "")
            decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
            lines = decoded.splitlines()
            start = max(comment_line - context_lines - 1, 0)
            end = min(comment_line + context_lines, len(lines))
            snippet_lines = lines[start:end]
            numbered = []
            for idx, line in enumerate(snippet_lines, start=start + 1):
                numbered.append(f"{idx:6d}: {line}")
            file_snippet = "\n".join(numbered)
        except Exception as exc:
            logger.warning(
                "pr_comment_file_context_failed",
                issue=pr_number,
                path=comment_path,
                error=str(exc),
            )

    payload = {
        "pr_number": pr_number,
        "repo": f"{repo_owner}/{repo_name}",
        "comment": {
            "body": comment_body,
            "path": comment_path,
            "line": comment_line,
        },
        "file_context": file_snippet or "File context unavailable.",
    }
    return json.dumps(payload, indent=2)

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
    work_type = state.metadata.get("work_type", "ready")
    context = {
        "repo_name": state.metadata.get("repo_name", "unknown"),
        "repo_owner": state.metadata.get("repo_owner", "unknown"),
        "issue_number": state.issue_number,
        "labels": state.issue.labels,
        "work_type": work_type,
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

    agents_md = ""
    agents_path = worktree_path / "AGENTS.md"
    if agents_path.exists():
        try:
            agents_md = agents_path.read_text(encoding="utf-8").strip()
        except Exception:
            agents_md = ""

    pr_context = None
    if work_type == "pr_comment" and repo_owner and repo_name and state.issue_number:
        pr_context = await _collect_pr_comment_context(
            settings,
            repo_owner,
            repo_name,
            state.issue_number,
            comment_body=state.metadata.get("pr_comment_body", ""),
            comment_path=state.metadata.get("pr_comment_path", ""),
            comment_line=state.metadata.get("pr_comment_line"),
        )

    instruction_builder = InstructionBuilder(backend=state.backend)
    instructions = await instruction_builder.build(
        state.issue,
        agents_md=agents_md or None,
        work_type=work_type,
        pr_context=pr_context,
    )
    instructions_path = Path(worktree_path) / "ACE_TASK.md"
    instructions_path.write_text(instructions, encoding="utf-8")
    context["instructions_path"] = str(instructions_path)
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
        log_key_event(
            logger,
            f"🧵 TMUX SESSION READY — ATTACH NOW: tmux attach -t {session_name}",
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
                marker: dict[str, Any] = {}
                try:
                    marker = json.loads(done_path.read_text(encoding="utf-8"))
                except Exception:
                    marker = {}
                summary = marker.get("summary", "")
                files_changed = marker.get("files_changed") if isinstance(marker.get("files_changed"), list) else []
                commands_run = marker.get("commands_run") if isinstance(marker.get("commands_run"), list) else []
                normalized_summary = " ".join(str(summary).lower().split())
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
                    error_message = summary or "Instruction refusal detected in ACE_TASK_DONE.json."
                    log_key_event(
                        logger,
                        f"❌ ERROR: {error_message}",
                        issue=state.issue_number,
                        path=str(done_path),
                    )
                    state.agent_result = AgentResult(
                        status=AgentStatus.FAILED,
                        output=error_message,
                        files_changed=files_changed,
                        commands_run=commands_run,
                        error="instruction_refusal",
                    )
                    state.error = state.agent_result.error
                    break
                log_key_event(
                    logger,
                    "✅ ACE_TASK_DONE.json found",
                    issue=state.issue_number,
                    task_id=marker.get("task_id"),
                    summary=summary,
                    files_changed_count=len(files_changed),
                    commands_run_count=len(commands_run),
                    path=str(done_path),
                )
                state.agent_result = AgentResult(
                    status=AgentStatus.SUCCESS,
                    output=summary or "Completed via CLI (ACE_TASK_DONE.json found).",
                    files_changed=files_changed,
                    commands_run=commands_run,
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
    done_path = workdir / "ACE_TASK_DONE.json" if workdir else None
    task_path = workdir / "ACE_TASK.md" if workdir else None

    status = "unknown"
    if done_path and done_path.exists():
        marker: dict[str, Any] = {}
        try:
            marker = json.loads(done_path.read_text(encoding="utf-8"))
        except Exception:
            marker = {}
        raw_status = str(marker.get("status") or marker.get("state") or "").lower()
        blocked_value = marker.get("blocked")
        blocked_questions = marker.get("blocked_questions")
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
