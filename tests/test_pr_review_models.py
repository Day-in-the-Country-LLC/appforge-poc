from datetime import UTC, datetime

from ace.pr_review import (
    PRReviewSession,
    PRReviewStatus,
    ReviewConfidence,
    ReviewFinding,
    ReviewVerdict,
    ReviewerVerdict,
)


def test_review_finding_roundtrip():
    finding = ReviewFinding(
        file_path="src/ace/webhooks/app.py",
        line_start=10,
        line_end=20,
        severity="blocking",
        category="security",
        description="Potential injection path detected",
        suggested_fix="Use parameterized query",
    )
    payload = finding.to_dict()
    recovered = ReviewFinding.from_dict(payload)
    assert recovered == finding


def test_reviewer_verdict_roundtrip():
    verdict = ReviewerVerdict(
        reviewer_model="gpt-5.2-codex",
        verdict=ReviewVerdict.APPROVE,
        confidence=ReviewConfidence.HIGH,
        blocking_findings=[
            ReviewFinding(
                file_path="src/ace/app.py",
                line_start=1,
                line_end=2,
                severity="suggestion",
                category="style",
                description="Spacing issue",
                suggested_fix="Run formatter",
            ),
        ],
        suggested_comments=["Looks good"],
        reasoning="Tests cover edge cases",
        round_number=1,
    )
    payload = verdict.to_dict()
    recovered = ReviewerVerdict.from_dict(payload)
    assert payload["verdict"] == "approve"
    assert payload["confidence"] == "high"
    assert recovered == verdict


def test_pr_review_session_roundtrip():
    codex_verdict = ReviewerVerdict(
        reviewer_model="gpt-5.2-codex",
        verdict=ReviewVerdict.COMMENT,
        confidence=ReviewConfidence.MEDIUM,
    )
    session = PRReviewSession(
        session_id="session-1",
        pr_number=101,
        repo_owner="acme",
        repo_name="widget-api",
        head_sha="abc123",
        base_branch="qa",
        created_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 18, 10, 6, 0, tzinfo=UTC),
        status=PRReviewStatus.IN_PROGRESS,
        rounds=[{"gpt-5.2-codex": codex_verdict}],
        final_codex_verdict=codex_verdict,
        final_claude_verdict=None,
        merged=False,
        error_message=None,
    )
    payload = session.to_dict()
    recovered = PRReviewSession.from_dict(payload)
    assert payload["status"] == "in_progress"
    assert payload["rounds"][0]["gpt-5.2-codex"]["verdict"] == "comment"
    assert recovered == session
