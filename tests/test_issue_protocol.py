"""Tests for the GitHub issue protocol."""

from ace.agents.policy import get_policy_prompt, prepend_policy_to_task


def test_policy_prompt_exists():
    """Test that policy prompt is defined."""
    prompt = get_policy_prompt()
    assert prompt is not None
    assert len(prompt) > 0
    assert "Never commit to main" in prompt


def test_prepend_policy_to_task():
    """Test that policy is prepended to task."""
    task = "Implement dark mode"
    result = prepend_policy_to_task(task)

    assert "Never commit to main" in result
    assert "Implement dark mode" in result
    assert result.index("Never commit to main") < result.index("Implement dark mode")
