"""GitHub action operations for collaborative PR review sessions."""

from __future__ import annotations

from typing import Any

import structlog

from ace.pr_review.mcp_bridge import PRReviewGitHubBridge


logger = structlog.get_logger(__name__)


class PRReviewActionError(ValueError):
    """Domain error for PR review action failures."""


def _format_approval_comment(session_result: Any, reviewer_label: str) -> str:
    verdict = session_result.verdict.value
    confidence = session_result.confidence.value
    reasoning = (session_result.reasoning or "").strip()
    lines = [
        f"### {reviewer_label} Approval",
        f"**Verdict:** {verdict}",
        f"**Confidence:** {confidence}",
    ]
    blocking = len(session_result.blocking_findings)
    non_blocking = len(session_result.non_blocking_findings)
    lines.append(f"**Findings:** {blocking} blocking, {non_blocking} non-blocking")
    if reasoning:
        lines.append("")
        lines.append("**Reasoning:**")
        lines.append(reasoning)
    return "\n".join(lines)


def _findings_section(findings: list[Any]) -> str:
    if not findings:
        return "None"
    lines = []
    for finding in findings:
        file_path = finding.file_path
        line = finding.line_start
        end_line = finding.line_end
        if line is None:
            location = file_path
        elif end_line is None or end_line == line:
            location = f"{file_path}:{line}"
        else:
            location = f"{file_path}:{line}-{end_line}"
        lines.append(
            f"- `{location}` · {finding.severity} · {finding.category}: {finding.description}"
        )
        if finding.suggested_fix:
            lines.append(f"  - Suggested fix: {finding.suggested_fix}")
    return "\n".join(lines)


def _format_round_transcript(session: Any) -> str:
    lines = []
    for index, round_item in enumerate(session.rounds, start=1):
        lines.append(f"### Round {index}")
        for model_name, verdict in round_item.items():
            lines.append(f"#### {model_name}")
            lines.append(f"- Verdict: {verdict.verdict.value}")
            lines.append(f"- Confidence: {verdict.confidence.value}")
            lines.append("- Blocking findings:")
            lines.append(_findings_section(verdict.blocking_findings))
            lines.append("- Non-blocking findings:")
            lines.append(_findings_section(verdict.non_blocking_findings))
            if verdict.reasoning:
                lines.append(f"- Reasoning: {verdict.reasoning}")
            lines.append("")
    if not lines:
        return "*No rounds recorded*"
    return "\n".join(lines)


async def submit_pr_approvals(
    session: Any,
    bridge: PRReviewGitHubBridge,
) -> dict[str, Any]:
    """Submit approval reviews from both configured reviewers."""
    if session.final_codex_verdict is None:
        raise PRReviewActionError("❌ ERROR: missing final Codex verdict")
    if session.final_claude_verdict is None:
        raise PRReviewActionError("❌ ERROR: missing final Claude verdict")

    codex_body = _format_approval_comment(session.final_codex_verdict, "gpt-5.2-codex")
    claude_body = _format_approval_comment(session.final_claude_verdict, "claude-opus-4.6")
    codex_result = await bridge.submit_pull_request_review(
        repo_owner=session.repo_owner,
        repo_name=session.repo_name,
        pr_number=session.pr_number,
        event="APPROVE",
        body=codex_body,
    )
    claude_result = await bridge.submit_pull_request_review(
        repo_owner=session.repo_owner,
        repo_name=session.repo_name,
        pr_number=session.pr_number,
        event="APPROVE",
        body=claude_body,
    )

    logger.info(
        "pr_review_approvals_submitted",
        repo=f"{session.repo_owner}/{session.repo_name}",
        pr_number=session.pr_number,
    )

    return {
        "codex": codex_result,
        "claude": claude_result,
    }


async def merge_pr_to_qa(
    session: Any,
    bridge: PRReviewGitHubBridge,
    *,
    require_checks: bool = True,
) -> dict[str, Any]:
    """Merge a PR to target branch after optional check validation."""
    pr_status = await bridge.get_pull_request_status(
        repo_owner=session.repo_owner,
        repo_name=session.repo_name,
        pr_number=session.pr_number,
    )
    if not isinstance(pr_status, dict):
        raise PRReviewActionError("❌ ERROR: pull request status payload must be an object")

    if pr_status.get("merged") is True:
        logger.info(
            "pr_review_merge_skipped_already_merged",
            repo=f"{session.repo_owner}/{session.repo_name}",
            pr_number=session.pr_number,
        )
        return {"merged": True, "reason": "already_merged"}

    if require_checks:
        checks_status = await bridge.get_combined_status_for_ref(
            repo_owner=session.repo_owner,
            repo_name=session.repo_name,
            ref=session.head_sha,
        )
        if checks_status.lower() != "success":
            logger.warning(
                "pr_review_merge_blocked_by_checks",
                repo=f"{session.repo_owner}/{session.repo_name}",
                pr_number=session.pr_number,
                head_sha=session.head_sha,
                checks_status=checks_status,
            )
            return {"merged": False, "reason": checks_status}

    merge_result = await bridge.merge_pull_request(
        repo_owner=session.repo_owner,
        repo_name=session.repo_name,
        pr_number=session.pr_number,
        sha=session.head_sha,
        merge_method="squash",
    )
    merged = bool(merge_result.get("merged"))
    logger.info(
        "pr_review_merge_completed",
        repo=f"{session.repo_owner}/{session.repo_name}",
        pr_number=session.pr_number,
        merged=merged,
    )

    return {
        "merged": merged,
        "reason": merge_result.get("message", "merged"),
        "merge_result": merge_result,
    }


async def post_review_summary_comment(
    session: Any,
    bridge: PRReviewGitHubBridge,
) -> Any:
    """Post a review outcome summary comment to the PR timeline."""
    if session.final_codex_verdict is None or session.final_claude_verdict is None:
        raise PRReviewActionError(
            "❌ ERROR: cannot post summary without both final verdicts"
        )

    codex_verdict = session.final_codex_verdict.verdict.value
    claude_verdict = session.final_claude_verdict.verdict.value
    codex_confidence = session.final_codex_verdict.confidence.value
    claude_confidence = session.final_claude_verdict.confidence.value
    outcome = "✅ Approved and merged to `qa`" if session.merged else "❌ Not merged"

    body = f"""## 🤖 Collaborative AI Review Summary

| Reviewer | Verdict | Confidence |
|----------|---------|------------|
| gpt-5.2-codex | {codex_verdict} | {codex_confidence} |
| claude-opus-4.6 | {claude_verdict} | {claude_confidence} |

**Outcome:** {outcome}

<details>
<summary>Review Transcript ({len(session.rounds)} rounds)</summary>

{_format_round_transcript(session)}

</details>

"""

    await bridge.post_issue_comment(
        repo_owner=session.repo_owner,
        repo_name=session.repo_name,
        issue_number=session.pr_number,
        body=body,
    )
    logger.info(
        "pr_review_summary_posted",
        repo=f"{session.repo_owner}/{session.repo_name}",
        pr_number=session.pr_number,
    )
    return {"posted": True}
