"""LangGraph orchestration graph definition."""

from datetime import datetime

import structlog
from langgraph.graph import StateGraph

from ace.orchestration.state import WorkerState

logger = structlog.get_logger(__name__)


async def fetch_candidates(state: WorkerState) -> WorkerState:
    """Fetch issues labeled agent:ready."""
    logger.info("step_fetch_candidates")
    state.current_step = "fetch_candidates"
    state.last_update = datetime.now()
    return state


async def claim_issue(state: WorkerState) -> WorkerState:
    """Claim an issue by adding labels and posting a claim comment."""
    logger.info("step_claim_issue", issue=state.issue_number)
    state.current_step = "claim_issue"
    state.last_update = datetime.now()
    return state


async def hydrate_context(state: WorkerState) -> WorkerState:
    """Pull issue body, key files, and repo metadata."""
    logger.info("step_hydrate_context", issue=state.issue_number)
    state.current_step = "hydrate_context"
    state.last_update = datetime.now()
    return state


async def select_backend(state: WorkerState) -> WorkerState:
    """Select execution backend (Codex vs Claude)."""
    logger.info("step_select_backend", issue=state.issue_number)
    state.current_step = "select_backend"
    state.backend = "codex"
    state.last_update = datetime.now()
    return state


async def run_agent(state: WorkerState) -> WorkerState:
    """Execute the agent in the workspace."""
    logger.info("step_run_agent", issue=state.issue_number, backend=state.backend)
    state.current_step = "run_agent"
    state.last_update = datetime.now()
    return state


async def evaluate_result(state: WorkerState) -> WorkerState:
    """Evaluate agent result and route to next step."""
    logger.info("step_evaluate_result", issue=state.issue_number)
    state.current_step = "evaluate_result"
    state.last_update = datetime.now()
    return state


async def handle_blocked(state: WorkerState) -> WorkerState:
    """Handle blocked state: post BLOCKED comment and wait."""
    logger.info("step_handle_blocked", issue=state.issue_number)
    state.current_step = "handle_blocked"
    state.last_update = datetime.now()
    return state


async def open_pr(state: WorkerState) -> WorkerState:
    """Open a pull request with the changes."""
    logger.info("step_open_pr", issue=state.issue_number)
    state.current_step = "open_pr"
    state.last_update = datetime.now()
    return state


async def post_failure(state: WorkerState) -> WorkerState:
    """Post failure comment and label issue as failed."""
    logger.info("step_post_failure", issue=state.issue_number, error=state.error)
    state.current_step = "post_failure"
    state.last_update = datetime.now()
    return state


async def mark_done(state: WorkerState) -> WorkerState:
    """Mark issue as done with PR link."""
    logger.info("step_mark_done", issue=state.issue_number, pr=state.pr_number)
    state.current_step = "mark_done"
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
