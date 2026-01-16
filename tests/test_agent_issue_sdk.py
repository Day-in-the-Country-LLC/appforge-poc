"""Tests for Agent Issue SDK."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_issue_creator_initialization(mock_fetch_secret):
    """Test IssueCreator initialization with credentials file."""
    mock_fetch_secret.return_value = "test_token"

    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project-id"}))

        creator = IssueCreator(
            github_org="test-org",
            project_name="test-project",
            credentials_file=str(creds_file),
        )

        assert creator.github_token == "test_token"
        assert creator.github_org == "test-org"
        assert creator.project_name == "test-project"
        mock_fetch_secret.assert_called_once_with(
            "test-project-id", "github-control-api-key", str(creds_file)
        )


def test_issue_creator_auto_detect_credentials():
    """Test IssueCreator auto-detects credentials file with *-creds.json pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            creds_file = Path(tmpdir) / "test-creds.json"
            creds_file.write_text(json.dumps({"project_id": "test-project"}))

            with patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret") as mock_fetch:
                mock_fetch.return_value = "test_token"
                creator = IssueCreator()
                assert creator.github_token == "test_token"
        finally:
            os.chdir(original_cwd)


def test_issue_creator_credentials_file_not_found():
    """Test IssueCreator raises error when no credentials file found."""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            try:
                IssueCreator()
                assert False, "Should have raised FileNotFoundError"
            except FileNotFoundError as e:
                assert "No credentials file found" in str(e)
        finally:
            os.chdir(original_cwd)


def test_issue_creator_invalid_credentials_json():
    """Test IssueCreator raises error with invalid JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text("invalid json {")

        try:
            IssueCreator(credentials_file=str(creds_file))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Invalid JSON" in str(e)


def test_issue_creator_missing_project_id():
    """Test IssueCreator raises error when project_id missing from credentials."""
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"type": "service_account"}))

        try:
            IssueCreator(credentials_file=str(creds_file))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "project_id not found" in str(e)


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_format_issue_body_basic(mock_fetch_secret):
    """Test basic issue body formatting."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

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


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_format_issue_body_with_notes(mock_fetch_secret):
    """Test issue body formatting with implementation notes."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

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


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_format_issue_body_with_related(mock_fetch_secret):
    """Test issue body formatting with related issues."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

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


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_build_labels_basic(mock_fetch_secret):
    """Test label building."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

        labels = creator._build_labels("medium", None)

        assert "agent" in labels
        assert "difficulty:medium" in labels


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_build_labels_with_additional(mock_fetch_secret):
    """Test label building with additional labels."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

        labels = creator._build_labels("hard", ["performance", "backend"])

        assert "agent" in labels
        assert "difficulty:hard" in labels
        assert "performance" in labels
        assert "backend" in labels


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_build_labels_all_difficulties(mock_fetch_secret):
    """Test label building for all difficulty levels."""
    mock_fetch_secret.return_value = "test_token"
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

        for difficulty in ("easy", "medium", "hard"):
            labels = creator._build_labels(difficulty, None)
            assert f"difficulty:{difficulty}" in labels


@patch("ace.agent_issue_sdk.client.IssueCreator._fetch_secret")
def test_issue_creator_headers(mock_fetch_secret):
    """Test that headers are correctly set."""
    token = "test_token_123"
    mock_fetch_secret.return_value = token
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_file = Path(tmpdir) / "creds.json"
        creds_file.write_text(json.dumps({"project_id": "test-project"}))
        creator = IssueCreator(credentials_file=str(creds_file))

        assert creator.headers["Authorization"] == f"token {token}"
        assert "Accept" in creator.headers
