"""LangGraph orchestration graph definition."""

from datetime import datetime

import structlog
from langgraph.graph import StateGraph

from ace.agents.model_selector import ModelSelector
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.github.status_manager import StatusManager
from ace.orchestration.state import WorkerState

logger = structlog.get_logger(__name__)


async def fetch_candidates(state: WorkerState) -> WorkerState:
    """Fetch issues labeled agent:ready."""
    logger.info("step_fetch_candidates")
    state.current_step = "fetch_candidates"
    state.last_update = datetime.now()
    return state


async def claim_issue(state: WorkerState) -> WorkerState:
    """Claim an issue: set status to In Progress, keep agent label."""
    logger.info("step_claim_issue", issue=state.issue_number)
    state.current_step = "claim_issue"

    if state.issue and state.branch_name:
        try:
            from ace.config.settings import get_settings

            settings = get_settings()
            api_client = GitHubAPIClient(settings.github_token)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            repo_name = state.metadata.get("repo", "unknown")
            await status_manager.claim_issue(state.issue_number, repo_name, state.branch_name)
        except Exception as e:
            logger.error("claim_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def hydrate_context(state: WorkerState) -> WorkerState:
    """Pull issue body, key files, and repo metadata."""
    logger.info("step_hydrate_context", issue=state.issue_number)
    state.current_step = "hydrate_context"
    state.last_update = datetime.now()
    return state


async def select_backend(state: WorkerState) -> WorkerState:
    """Select execution backend based on issue difficulty."""
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
    """Execute the agent in the workspace."""
    logger.info("step_run_agent", issue=state.issue_number, backend=state.backend)
    state.current_step = "run_agent"

    if not state.issue:
        logger.error("no_issue_to_process", issue=state.issue_number)
        state.last_update = datetime.now()
        return state

    try:
        # Build task from issue
        task = f"{state.issue.title}\n\n{state.issue.body}"
        context = {
            "repo_name": state.metadata.get("repo_name", "unknown"),
            "repo_owner": state.metadata.get("repo_owner", "unknown"),
            "issue_number": state.issue_number,
            "labels": state.issue.labels,
        }
        workspace_path = state.workspace_path or "/tmp/agent-workspace"

        # Select and run the appropriate agent
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

        result = await agent.run(task, context, workspace_path)
        state.agent_result = result

        logger.info(
            "agent_execution_complete",
            issue=state.issue_number,
            status=result.status.value,
            output_length=len(result.output),
        )

    except Exception as e:
        logger.error("agent_execution_failed", issue=state.issue_number, error=str(e))
        from ace.agents.base import AgentResult, AgentStatus

        state.agent_result = AgentResult(
            status=AgentStatus.FAILED,
            output="",
            files_changed=[],
            commands_run=[],
            error=str(e),
        )

    state.last_update = datetime.now()
    return state


async def evaluate_result(state: WorkerState) -> WorkerState:
    """Evaluate agent result and route to next step."""
    logger.info("step_evaluate_result", issue=state.issue_number)
    state.current_step = "evaluate_result"
    state.last_update = datetime.now()
    return state


async def handle_blocked(state: WorkerState) -> WorkerState:
    """Handle blocked state: remove agent label, assign to user, post questions."""
    logger.info("step_handle_blocked", issue=state.issue_number)
    state.current_step = "handle_blocked"

    if state.agent_result and state.agent_result.blocked_questions:
        try:
            from ace.config.settings import get_settings

            settings = get_settings()
            api_client = GitHubAPIClient(settings.github_token)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            await status_manager.mark_blocked(
                state.issue_number,
                state.agent_result.blocked_questions,
                assignee="kristinday",
            )
        except Exception as e:
            logger.error("mark_blocked_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def open_pr(state: WorkerState) -> WorkerState:
    """Open a pull request with the changes and send SMS notification."""
    logger.info("step_open_pr", issue=state.issue_number)
    state.current_step = "open_pr"

    if state.pr_number and state.pr_url and state.issue:
        try:
            from ace.notifications.twilio_client import TwilioNotifier

            notifier = TwilioNotifier()

            summary = state.agent_result.output if state.agent_result else "Work completed"
            repo_name = state.metadata.get("repo", "unknown")

            await notifier.send_pr_notification(
                pr_number=state.pr_number,
                pr_url=state.pr_url,
                issue_number=state.issue_number,
                issue_title=state.issue.title,
                repo_name=repo_name,
                summary=summary[:200],
            )
        except Exception as e:
            logger.error("pr_notification_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def post_failure(state: WorkerState) -> WorkerState:
    """Post failure comment: remove agent label, assign to user, post error."""
    logger.info("step_post_failure", issue=state.issue_number, error=state.error)
    state.current_step = "post_failure"

    if state.issue_number and state.error:
        try:
            from ace.config.settings import get_settings

            settings = get_settings()
            api_client = GitHubAPIClient(settings.github_token)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            await status_manager.mark_failed(state.issue_number, state.error)
        except Exception as e:
            logger.error("mark_failed_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


async def mark_done(state: WorkerState) -> WorkerState:
    """Mark issue as done: set status to Done, remove agent label, post PR link."""
    logger.info("step_mark_done", issue=state.issue_number, pr=state.pr_number)
    state.current_step = "mark_done"

    if state.issue_number and state.pr_number and state.pr_url:
        try:
            from ace.config.settings import get_settings

            settings = get_settings()
            api_client = GitHubAPIClient(settings.github_token)
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(api_client, settings.github_org, "", projects_client)
            status_manager = StatusManager(issue_queue)

            await status_manager.mark_done(state.issue_number, state.pr_number, state.pr_url)
        except Exception as e:
            logger.error("mark_done_failed", issue=state.issue_number, error=str(e))

    state.last_update = datetime.now()
    return state


def create_workflow_graph() -> StateGraph:
    """Create the LangGraph workflow graph."""
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
        """Route based on agent result."""
        if state.agent_result and state.agent_result.blocked_questions:
            return "handle_blocked"
        elif state.agent_result and state.agent_result.status.value == "success":
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
    """Get the compiled workflow graph."""
    workflow = create_workflow_graph()
    return workflow.compile()
