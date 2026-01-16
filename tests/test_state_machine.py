"""Tests for the state machine workflow."""

import pytest

from ace.orchestration.graph import create_workflow_graph
from ace.orchestration.state import WorkerState


@pytest.mark.asyncio
async def test_workflow_graph_creation():
    """Test that the workflow graph can be created."""
    graph = create_workflow_graph()
    assert graph is not None


@pytest.mark.asyncio
async def test_initial_state():
    """Test that initial state is properly initialized."""
    state = WorkerState(
        issue_number=123,
        agent_id="test-agent",
        workspace_path="/tmp/test",
        branch_name="agent/123-test",
    )

    assert state.issue_number == 123
    assert state.agent_id == "test-agent"
    assert state.current_step == ""
    assert state.error is None


def test_state_to_dict():
    """Test state serialization to dict."""
    state = WorkerState(
        issue_number=456,
        agent_id="test-agent",
        workspace_path="/tmp/test",
        branch_name="agent/456-test",
        pr_number=789,
    )

    state_dict = state.to_dict()

    assert state_dict["issue_number"] == 456
    assert state_dict["agent_id"] == "test-agent"
    assert state_dict["pr_number"] == 789
