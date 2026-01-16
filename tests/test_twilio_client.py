"""Tests for Twilio notification client."""

from ace.notifications.twilio_client import TwilioNotifier


def test_twilio_notifier_initialization():
    """Test TwilioNotifier initialization."""
    notifier = TwilioNotifier()
    assert notifier is not None


def test_format_pr_message():
    """Test PR notification message formatting."""
    notifier = TwilioNotifier()

    message = notifier._format_pr_message(
        pr_number=789,
        pr_url="https://github.com/org/repo/pull/789",
        issue_number=42,
        issue_title="Add dark mode support",
        repo_name="frontend-repo",
        summary="Implemented dark mode toggle with persistent storage",
    )

    assert "PR Ready for Review" in message
    assert "#42" in message
    assert "Add dark mode support" in message
    assert "frontend-repo" in message
    assert "#789" in message
    assert "https://github.com/org/repo/pull/789" in message
    assert "Implemented dark mode toggle" in message


def test_format_blocked_message():
    """Test blocked notification message formatting."""
    notifier = TwilioNotifier()

    message = notifier._format_blocked_message(
        issue_number=42,
        issue_title="Add dark mode support",
        question="Should dark mode be opt-in or default?",
    )

    assert "Agent Blocked" in message
    assert "#42" in message
    assert "Add dark mode support" in message
    assert "Should dark mode be opt-in or default?" in message
    assert "GitHub" in message


def test_pr_message_length():
    """Test that PR message fits in SMS character limit."""
    notifier = TwilioNotifier()

    message = notifier._format_pr_message(
        pr_number=789,
        pr_url="https://github.com/org/repo/pull/789",
        issue_number=42,
        issue_title="Add dark mode support",
        repo_name="frontend-repo",
        summary="x" * 200,
    )

    assert len(message) <= 1600


def test_blocked_message_length():
    """Test that blocked message fits in SMS character limit."""
    notifier = TwilioNotifier()

    message = notifier._format_blocked_message(
        issue_number=42,
        issue_title="Add dark mode support",
        question="x" * 100,
    )

    assert len(message) <= 1600
