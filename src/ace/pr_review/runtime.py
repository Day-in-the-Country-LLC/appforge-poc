"""Runtime orchestration for collaborative PR reviews."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from ace.agents.llm_client import call_claude, call_openai
from ace.config.secrets import resolve_claude_api_key, resolve_openai_api_key
from ace.config.settings import Settings
from ace.github.issue_queue import IssueQueue
from ace.pr_review.actions import (
    PRReviewActionError,
    merge_pr_to_qa,
    post_review_summary_comment,
    submit_pr_approvals,
)
from ace.pr_review.context import PRContext, gather_pr_context
from ace.pr_review.job_store import PRReviewSessionStore, PRReviewSessionStoreError
from ace.pr_review.mcp_bridge import PRReviewGitHubBridge
from ace.pr_review.models import (
    PRReviewSession,
    PRReviewStatus,
    ReviewVerdict,
    ReviewerVerdict,
)
from ace.pr_review.prompts import (
    CROSS_FEEDBACK_PROMPT_TEMPLATE,
    REVIEWER_PROMPT_TEMPLATE,
)
from ace.webhooks.lifecycle import (
    STAGE_PR_REVIEW_CLAUDE_INITIAL,
    STAGE_PR_REVIEW_CODEX_INITIAL,
    STAGE_PR_REVIEW_CONSENSUS,
    STAGE_PR_REVIEW_CROSS_FEEDBACK,
    STAGE_PR_REVIEW_ERROR,
    STAGE_PR_REVIEW_APPROVAL_SUBMITTED,
    STAGE_PR_REVIEW_MERGED,
    STAGE_PR_REVIEW_REJECTED,
    WebhookLifecycleContext,
    build_lifecycle_context,
    log_lifecycle_event,
)


logger = structlog.get_logger(__name__)


class PRReviewRuntimeError(ValueError):
    """Domain error for review orchestration failures."""


class PRReviewRuntime:
    """Orchestrates dual-model PR review rounds and consensus decisions."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: PRReviewSessionStore,
        issue_queue: IssueQueue,
        openai_api_key: str | None = None,
        claude_api_key: str | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.issue_queue = issue_queue
        self.openai_api_key = (
            openai_api_key if openai_api_key is not None else resolve_openai_api_key(settings)
        )
        self.claude_api_key = (
            claude_api_key if claude_api_key is not None else resolve_claude_api_key(settings)
        )

    async def run_review(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        head_sha: str,
        base_branch: str,
        action: str,
        workflow_id: str,
        session_id: str | None = None,
    ) -> PRReviewSession:
        if self.settings.pr_review_max_rounds < 1:
            raise PRReviewRuntimeError(
                "❌ ERROR: PR_REVIEW_MAX_ROUNDS must be at least 1"
            )

        lifecycle_context = _build_context_for_lifecycle(
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
            action=action,
            workflow_id=workflow_id,
        )
        effective_session_id = session_id or _build_session_id(
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
            head_sha=head_sha,
        )

        existing = await self.store.get_session(effective_session_id)
        if existing is not None:
            if existing.status != PRReviewStatus.IN_PROGRESS:
                return existing
            session = existing
        else:
            session = PRReviewSession(
                session_id=effective_session_id,
                pr_number=pr_number,
                repo_owner=repo_owner,
                repo_name=repo_name,
                head_sha=head_sha,
                base_branch=base_branch,
                created_at=_utc_now(),
                updated_at=_utc_now(),
                status=PRReviewStatus.IN_PROGRESS,
                rounds=[],
                final_codex_verdict=None,
                final_claude_verdict=None,
                merged=False,
                error_message=None,
            )
            await self.store.create_session(session)

        try:
            context = await gather_pr_context(
                self.issue_queue,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                max_diff_chars=self.settings.pr_review_diff_max_chars,
            )

            codex_verdict = await self._review(
                reviewer_model=self.settings.pr_review_codex_model,
                context=context,
                round_number=1,
                stage=STAGE_PR_REVIEW_CODEX_INITIAL,
                lifecycle_context=lifecycle_context,
            )
            claude_verdict = await self._review(
                reviewer_model=self.settings.pr_review_claude_model,
                context=context,
                round_number=1,
                stage=STAGE_PR_REVIEW_CLAUDE_INITIAL,
                lifecycle_context=lifecycle_context,
            )

            session.rounds = [
                {
                    self.settings.pr_review_codex_model: codex_verdict,
                    self.settings.pr_review_claude_model: claude_verdict,
                }
            ]
            session.final_codex_verdict = codex_verdict
            session.final_claude_verdict = claude_verdict
            session.status = _resolve_consensus(
                consensus_mode=self.settings.pr_review_consensus_mode,
                codex=codex_verdict,
                claude=claude_verdict,
            )
            session.updated_at = _utc_now()
            await self.store.update_session(session)
            if session.status == PRReviewStatus.CONSENSUS_APPROVE:
                log_lifecycle_event(
                    logger,
                    STAGE_PR_REVIEW_CONSENSUS,
                    lifecycle_context,
                    decision="consensus_approve",
                    rounds=len(session.rounds),
                )
                await self._apply_consensus_actions(
                    session=session,
                    lifecycle_context=lifecycle_context,
                )
                return session

            max_rounds = self.settings.pr_review_max_rounds
            for round_number in range(2, max_rounds + 1):
                next_codex_verdict = await self._review(
                    reviewer_model=self.settings.pr_review_codex_model,
                    context=context,
                    round_number=round_number,
                    prior_verdict=codex_verdict,
                    other_verdict=claude_verdict,
                    stage=STAGE_PR_REVIEW_CROSS_FEEDBACK,
                    lifecycle_context=lifecycle_context,
                )
                next_claude_verdict = await self._review(
                    reviewer_model=self.settings.pr_review_claude_model,
                    context=context,
                    round_number=round_number,
                    prior_verdict=claude_verdict,
                    other_verdict=codex_verdict,
                    stage=STAGE_PR_REVIEW_CROSS_FEEDBACK,
                    lifecycle_context=lifecycle_context,
                )
                codex_verdict = next_codex_verdict
                claude_verdict = next_claude_verdict

                session.rounds.append(
                    {
                        self.settings.pr_review_codex_model: codex_verdict,
                        self.settings.pr_review_claude_model: claude_verdict,
                    }
                )
                session.final_codex_verdict = codex_verdict
                session.final_claude_verdict = claude_verdict
                session.status = _resolve_consensus(
                    consensus_mode=self.settings.pr_review_consensus_mode,
                    codex=codex_verdict,
                    claude=claude_verdict,
                )
                session.updated_at = _utc_now()
                await self.store.update_session(session)
                if session.status == PRReviewStatus.CONSENSUS_APPROVE:
                    log_lifecycle_event(
                        logger,
                        STAGE_PR_REVIEW_CONSENSUS,
                        lifecycle_context,
                        decision="consensus_approve",
                        rounds=len(session.rounds),
                    )
                    await self._apply_consensus_actions(
                        session=session,
                        lifecycle_context=lifecycle_context,
                    )
                    break

            log_lifecycle_event(
                logger,
                STAGE_PR_REVIEW_CONSENSUS,
                lifecycle_context,
                decision=session.status.value,
                rounds=len(session.rounds),
            )
            if session.status == PRReviewStatus.CONSENSUS_REJECT:
                log_lifecycle_event(
                    logger,
                    STAGE_PR_REVIEW_REJECTED,
                    lifecycle_context,
                    session_id=session.session_id,
                )
            return session
        except Exception as exc:
            session.status = PRReviewStatus.ERROR
            session.error_message = str(exc)
            session.updated_at = _utc_now()
            await self.store.update_session(session)
            log_lifecycle_event(
                logger,
                STAGE_PR_REVIEW_ERROR,
                lifecycle_context,
                session_id=session.session_id,
                error=f"❌ ERROR: {exc}",
            )
            raise PRReviewRuntimeError(f"❌ ERROR: PR review runtime failed: {exc}") from exc

    async def _review(
        self,
        *,
        reviewer_model: str,
        context: PRContext,
        round_number: int,
        lifecycle_context: WebhookLifecycleContext,
        stage: str,
        prior_verdict: ReviewerVerdict | None = None,
        other_verdict: ReviewerVerdict | None = None,
    ) -> ReviewerVerdict:
        prompt = _build_prompt(
            context=context,
            round_number=round_number,
            reviewer_model=reviewer_model,
            prior_verdict=prior_verdict,
            other_verdict=other_verdict,
        )
        log_lifecycle_event(
            logger,
            stage,
            lifecycle_context,
            round_number=round_number,
            model=reviewer_model,
            reviewer=reviewer_model,
            prior_round=prior_verdict.round_number if prior_verdict else None,
        )

        if reviewer_model == self.settings.pr_review_codex_model:
            response = await call_openai(
                prompt=prompt,
                model=self.settings.pr_review_codex_model,
                api_key=self.openai_api_key,
                max_tokens=self.settings.pr_review_codex_max_tokens,
                reasoning_effort=self.settings.pr_review_codex_reasoning_effort,
            )
        else:
            response = await call_claude(
                prompt=prompt,
                model=self.settings.pr_review_claude_model,
                api_key=self.claude_api_key,
                max_tokens=self.settings.pr_review_claude_max_tokens,
            )

        verdict_payload = _parse_verdict_payload(response)
        verdict = ReviewerVerdict.from_dict(verdict_payload)
        verdict.reviewer_model = reviewer_model
        verdict.round_number = round_number
        return verdict

    async def _apply_consensus_actions(
        self,
        *,
        session: PRReviewSession,
        lifecycle_context: WebhookLifecycleContext,
    ) -> None:
        bridge = PRReviewGitHubBridge(self.issue_queue)
        try:
            approval_result = await submit_pr_approvals(session=session, bridge=bridge)
            log_lifecycle_event(
                logger,
                STAGE_PR_REVIEW_APPROVAL_SUBMITTED,
                lifecycle_context,
                session_id=session.session_id,
                codex_review_id=approval_result["codex"].get("id"),
                claude_review_id=approval_result["claude"].get("id"),
            )
        except Exception as exc:
            raise PRReviewActionError(
                f"❌ ERROR: failed to submit review approvals: {exc}"
            ) from exc

        if session.base_branch == self.settings.pr_review_target_branch:
            try:
                merge_result = await merge_pr_to_qa(
                    session=session,
                    bridge=bridge,
                    require_checks=self.settings.pr_review_require_checks,
                )
                session.merged = bool(merge_result.get("merged"))
            except PRReviewActionError:
                raise
            except Exception as exc:
                raise PRReviewActionError(
                    f"❌ ERROR: PR merge action failed: {exc}"
                ) from exc

            if session.merged:
                log_lifecycle_event(
                    logger,
                    STAGE_PR_REVIEW_MERGED,
                    lifecycle_context,
                    session_id=session.session_id,
                    reason=merge_result.get("reason"),
                )
            else:
                logger.warning(
                    "pr_review_merge_skipped",
                    repo=f"{session.repo_owner}/{session.repo_name}",
                    pr_number=session.pr_number,
                    head_sha=session.head_sha,
                    reason=merge_result.get("reason"),
                )
        else:
            logger.info(
                "pr_review_merge_skipped_target_mismatch",
                repo=f"{session.repo_owner}/{session.repo_name}",
                pr_number=session.pr_number,
                base_branch=session.base_branch,
                expected_branch=self.settings.pr_review_target_branch,
            )

        await post_review_summary_comment(session=session, bridge=bridge)
        session.updated_at = _utc_now()
        await self.store.update_session(session)


async def _noop(_: Any) -> None:
    return None


def _build_session_id(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
) -> str:
    return f"{repo_owner}/{repo_name}:{pr_number}:{head_sha}"


def _build_context_for_lifecycle(
    *,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    action: str,
    workflow_id: str,
) -> WebhookLifecycleContext:
    return build_lifecycle_context(
        event="pull_request",
        payload={
            "action": action,
            "number": pr_number,
            "repository": {
                "name": repo_name,
                "owner": {"login": repo_owner},
            },
        },
        delivery=workflow_id,
        workflow_id=workflow_id,
    )


def _build_prompt(
    *,
    context: PRContext,
    round_number: int,
    reviewer_model: str,
    prior_verdict: ReviewerVerdict | None = None,
    other_verdict: ReviewerVerdict | None = None,
) -> str:
    context_json = json.dumps(context.to_dict(), ensure_ascii=True, indent=2)
    if prior_verdict is None or other_verdict is None:
        return REVIEWER_PROMPT_TEMPLATE.substitute(
            round_number=round_number,
            reviewer_model=reviewer_model,
            context_json=context_json,
        )
    return CROSS_FEEDBACK_PROMPT_TEMPLATE.substitute(
        prior_verdict_json=json.dumps(prior_verdict.to_dict(), ensure_ascii=True, indent=2),
        other_verdict_json=json.dumps(other_verdict.to_dict(), ensure_ascii=True, indent=2),
        round_number=round_number,
        reviewer_model=reviewer_model,
        context_json=context_json,
    )


def _resolve_consensus(
    *,
    consensus_mode: str,
    codex: ReviewerVerdict,
    claude: ReviewerVerdict,
) -> PRReviewStatus:
    if consensus_mode == "both_approve":
        if (
            codex.verdict == ReviewVerdict.APPROVE
            and claude.verdict == ReviewVerdict.APPROVE
            and not codex.blocking_findings
            and not claude.blocking_findings
        ):
            return PRReviewStatus.CONSENSUS_APPROVE
        return PRReviewStatus.CONSENSUS_REJECT
    raise PRReviewRuntimeError(
        f"❌ ERROR: unsupported consensus mode '{consensus_mode}'"
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_verdict_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        verdict_payload: dict[str, Any] = payload
    elif isinstance(payload, str):
        verdict_payload = json.loads(payload)
    else:
        raise PRReviewRuntimeError(
            "❌ ERROR: PR review verdict payload must be JSON text or dictionary"
        )
    if not isinstance(verdict_payload, dict):
        raise PRReviewRuntimeError("❌ ERROR: PR review verdict payload must be an object")
    return verdict_payload
