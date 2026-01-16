"""Tests for Agent Issue SDK."""

from ace.agent_issue_sdk import IssueContent, IssueCreator


def test_issue_content_creation():
    """Test IssueContent dataclass creation."""
    issue = IssueContent(
        title="Add feature",
        target_repository="test-repo",
        description="Test description",
        acceptance_criteria=["Criterion 1", "Criterion 2"],
    )

    assert issue.title == "Add feature"
    assert issue.target_repository == "test-repo"
    assert issue.description == "Test description"
    assert len(issue.acceptance_criteria) == 2


def test_issue_creator_initialization():
    """Test IssueCreator initialization."""
    creator = IssueCreator(
        github_token="test_token",
        github_org="test-org",
        project_name="test-project",
    )

    assert creator.github_token == "test_token"
    assert creator.github_org == "test-org"
    assert creator.project_name == "test-project"


def test_format_issue_body_basic():
    """Test basic issue body formatting."""
    creator = IssueCreator(github_token="test_token")

    issue = IssueContent(
        title="Test Issue",
        target_repository="test-repo",
        description="This is a test",
        acceptance_criteria=["Criterion 1", "Criterion 2"],
    )

    body = creator._format_issue_body(issue)

    assert "## Target Repository" in body
    assert "test-repo" in body
    assert "## Description" in body
    assert "This is a test" in body
    assert "## Acceptance Criteria" in body
    assert "- [ ] Criterion 1" in body
    assert "- [ ] Criterion 2" in body


def test_format_issue_body_with_notes():
    """Test issue body formatting with implementation notes."""
    creator = IssueCreator(github_token="test_token")

    issue = IssueContent(
        title="Test Issue",
        target_repository="test-repo",
        description="This is a test",
        acceptance_criteria=["Criterion 1"],
        implementation_notes="Use pattern X from utils.py",
    )

    body = creator._format_issue_body(issue)

    assert "## Implementation Notes" in body
    assert "Use pattern X from utils.py" in body


def test_format_issue_body_with_related():
    """Test issue body formatting with related issues."""
    creator = IssueCreator(github_token="test_token")

    issue = IssueContent(
        title="Test Issue",
        target_repository="test-repo",
        description="This is a test",
        acceptance_criteria=["Criterion 1"],
        related_issues=["#123", "#456"],
    )

    body = creator._format_issue_body(issue)

    assert "## Related Issues" in body
    assert "- #123" in body
    assert "- #456" in body


def test_build_labels_basic():
    """Test label building."""
    creator = IssueCreator(github_token="test_token")

    labels = creator._build_labels("medium", None)

    assert "agent" in labels
    assert "difficulty:medium" in labels


def test_build_labels_with_additional():
    """Test label building with additional labels."""
    creator = IssueCreator(github_token="test_token")

    labels = creator._build_labels("hard", ["performance", "backend"])

    assert "agent" in labels
    assert "difficulty:hard" in labels
    assert "performance" in labels
    assert "backend" in labels


def test_build_labels_all_difficulties():
    """Test label building for all difficulty levels."""
    creator = IssueCreator(github_token="test_token")

    for difficulty in ("easy", "medium", "hard"):
        labels = creator._build_labels(difficulty, None)
        assert f"difficulty:{difficulty}" in labels


def test_issue_creator_headers():
    """Test that headers are correctly set."""
    token = "test_token_123"
    creator = IssueCreator(github_token=token)

    assert creator.headers["Authorization"] == f"token {token}"
    assert "Accept" in creator.headers
