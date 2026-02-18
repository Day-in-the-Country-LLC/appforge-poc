"""Runtime orchestration for planning intake and worker execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from ace.config.settings import Settings
from ace.planning.models import (
    PlanningArtifact,
    PlanningArtifactType,
    PlanningEvent,
    PlanningMessage,
    PlanningSession,
)
from ace.planning.pubsub_queue import QueuedPlanningJob
from ace.planning.store_firestore import (
    PLANNING_STATUS_DONE,
    PLANNING_STATUS_FAILED,
    PLANNING_STATUS_RUNNING,
)
from ace.webhooks.lifecycle import (
    STAGE_PLANNING_DONE,
    STAGE_PLANNING_FAILED,
    STAGE_PLANNING_ISSUE_WRITER,
    STAGE_PLANNING_REVIEW,
    STAGE_PLANNING_SCOUTING,
    STAGE_PLANNING_SYNTHESIS,
)


@dataclass(frozen=True)
class IntakeAgentDecision:
    """Single intake action returned by the intake LLM."""

    action: str
    assistant_message: str
    repo_question: str | None = None


class IntakeRuntime:
    """Stateful runtime for one intake turn."""

    def __init__(
        self,
        *,
        max_internal_actions: int,
        ask_user_action: str,
        ask_repo_agents_action: str,
        ready_to_plan_action: str,
        normalized_intake_state: Callable[[PlanningSession], dict[str, Any]],
        request_decision: Callable[..., Awaitable[IntakeAgentDecision]],
        run_repo_agents: Callable[..., Awaitable[None]],
        build_planning_context: Callable[..., str],
        refresh_session_state: Callable[[PlanningSession], Awaitable[PlanningSession]],
        append_assistant_message: Callable[..., Awaitable[PlanningMessage]],
        start_planning: Callable[[str], Awaitable[Any]],
        ensure_session: Callable[[str], Awaitable[PlanningSession]],
        utc_now: Callable[[], datetime],
    ) -> None:
        self._max_internal_actions = max_internal_actions
        self._ask_user_action = ask_user_action
        self._ask_repo_agents_action = ask_repo_agents_action
        self._ready_to_plan_action = ready_to_plan_action
        self._normalized_intake_state = normalized_intake_state
        self._request_decision = request_decision
        self._run_repo_agents = run_repo_agents
        self._build_planning_context = build_planning_context
        self._refresh_session_state = refresh_session_state
        self._append_assistant_message = append_assistant_message
        self._start_planning = start_planning
        self._ensure_session = ensure_session
        self._utc_now = utc_now

    async def run_turn(
        self,
        *,
        store: Any,
        session: PlanningSession,
        settings: Settings,
    ) -> PlanningMessage:
        if settings.planning_intake_max_repo_scouts_per_turn < 1:
            raise ValueError("❌ ERROR: PLANNING_INTAKE_MAX_REPO_SCOUTS_PER_TURN must be >= 1")

        state = self._normalized_intake_state(session)
        repo_scout_calls_this_turn = 0

        for _ in range(self._max_internal_actions):
            state["agent_turn"] = int(state.get("agent_turn", 0)) + 1
            messages = await store.get_messages(session.id)
            decision = await self._request_decision(
                session=session,
                messages=messages,
                state=state,
                settings=settings,
            )

            if decision.action == self._ask_repo_agents_action:
                if repo_scout_calls_this_turn >= settings.planning_intake_max_repo_scouts_per_turn:
                    raise ValueError(
                        "❌ ERROR: intake agent exceeded max repo scouts for this user turn "
                        f"({settings.planning_intake_max_repo_scouts_per_turn})"
                    )
                repo_scout_calls_this_turn += 1
                await self._run_repo_agents(
                    store=store,
                    session=session,
                    state=state,
                    repo_question=decision.repo_question or "",
                    settings=settings,
                )
                continue

            if decision.action == self._ready_to_plan_action:
                state["ready_to_plan"] = True
                state["planning_context"] = self._build_planning_context(
                    messages=messages,
                    state=state,
                    settings=settings,
                )
                session.intake_state = state
                session.updated_at = self._utc_now()
                session = await self._refresh_session_state(session)
                await store.update_session(session)
                await self._start_planning(session.id)
                session = await self._ensure_session(session.id)
                return await self._append_assistant_message(
                    store=store,
                    session=session,
                    content=decision.assistant_message,
                    event_type="intake_agent_complete",
                )

            if decision.action != self._ask_user_action:
                raise ValueError(f"❌ ERROR: intake runtime received unknown action '{decision.action}'")

            session.intake_state = state
            session.updated_at = self._utc_now()
            session = await self._refresh_session_state(session)
            await store.update_session(session)
            return await self._append_assistant_message(
                store=store,
                session=session,
                content=decision.assistant_message,
                event_type="intake_agent_prompt",
            )

        raise ValueError("❌ ERROR: intake agent exceeded maximum internal actions for one user turn")


class PlannerWorkerRuntime:
    """Runtime orchestration for planner worker jobs."""

    def __init__(
        self,
        *,
        coerce_pipeline_output: Callable[[Any], tuple[list[tuple[PlanningArtifactType, str]], dict[PlanningArtifactType, str]]],
        review_and_update_issues: Callable[..., Awaitable[tuple[str, dict[str, Any]]]],
        write_issues_from_payload: Callable[..., Awaitable[list[Any]]],
        resolve_artifact_store: Callable[[], Any],
        log_planning_lifecycle_event: Callable[..., None],
        utc_now: Callable[[], datetime],
    ) -> None:
        self._coerce_pipeline_output = coerce_pipeline_output
        self._review_and_update_issues = review_and_update_issues
        self._write_issues_from_payload = write_issues_from_payload
        self._resolve_artifact_store = resolve_artifact_store
        self._log = log_planning_lifecycle_event
        self._utc_now = utc_now

    async def run(
        self,
        *,
        store: Any,
        session: PlanningSession,
        queued: QueuedPlanningJob,
        planner_pipeline: Callable[[PlanningSession], Awaitable[Any]],
        settings: Settings,
        github_token: str,
    ) -> dict[str, Any]:
        session.status = PLANNING_STATUS_RUNNING
        session.updated_at = self._utc_now()
        persisted_artifacts: list[PlanningArtifact] = []
        issues_created: list[dict[str, Any]] = []
        response_artifacts: list[str] = []

        try:
            self._log(
                session,
                stage=STAGE_PLANNING_SCOUTING,
                phase="scouting",
                event_type="running",
                status=PLANNING_STATUS_RUNNING,
                message_id=queued.message_id,
            )
            await store.append_event(
                session.id,
                PlanningEvent(
                    session_id=session.id,
                    event_type="running",
                    payload={
                        "status": PLANNING_STATUS_RUNNING,
                        "message_id": queued.message_id,
                    },
                ),
            )
            await store.update_session(session)

            pipeline_output = await planner_pipeline(session)
            artifact_rows, artifact_payloads = self._coerce_pipeline_output(pipeline_output)
            for artifact_type, content_url in artifact_rows:
                self._log(
                    session,
                    stage=STAGE_PLANNING_SYNTHESIS,
                    phase="synthesis",
                    event_type="artifact_writing",
                    artifact_type=artifact_type.value,
                )
                artifact = PlanningArtifact(
                    session_id=session.id,
                    artifact_type=artifact_type,
                    content_url=content_url,
                )
                await store.add_artifact(session.id, artifact)
                persisted_artifacts.append(artifact)
                self._log(
                    session,
                    stage=STAGE_PLANNING_SYNTHESIS,
                    phase="synthesis",
                    event_type="artifact_written",
                    artifact_type=artifact_type.value,
                    artifact_id=artifact.id,
                    artifact_url=content_url,
                )

            issues_json = artifact_payloads.get(PlanningArtifactType.ISSUES_JSON, '{"issues": []}')
            plan_markdown = artifact_payloads.get(PlanningArtifactType.PLAN_MARKDOWN, "")
            self._log(
                session,
                stage=STAGE_PLANNING_REVIEW,
                phase="review",
                event_type="issues_review_started",
                message_id=queued.message_id,
            )
            try:
                reviewed_issues_json, review_summary = await self._review_and_update_issues(
                    session=session,
                    plan_markdown=plan_markdown,
                    issues_json=issues_json,
                    settings=settings,
                )
            except Exception as exc:
                self._log(
                    session,
                    stage=STAGE_PLANNING_REVIEW,
                    phase="review",
                    event_type="issues_review_failed",
                    message_id=queued.message_id,
                    error=str(exc),
                )
                await store.append_event(
                    session.id,
                    PlanningEvent(
                        session_id=session.id,
                        event_type="issues_review_failed",
                        payload={
                            "status": "failed",
                            "error": str(exc),
                        },
                    ),
                )
                raise ValueError(f"❌ ERROR: planning review failed: {exc}") from exc

            self._log(
                session,
                stage=STAGE_PLANNING_REVIEW,
                phase="review",
                event_type="issues_review_complete",
                message_id=queued.message_id,
                review_plan_recommendations=review_summary.get("plan_recommendations_count"),
                review_issue_recommendations=review_summary.get("issue_recommendations_count"),
                review_overall_feedback=str(review_summary.get("overall_feedback", "")).strip(),
                review_elapsed_seconds=review_summary.get("elapsed_seconds"),
            )
            review_artifact_content = json.dumps(review_summary, indent=2)
            artifact_store = self._resolve_artifact_store()
            review_url = await artifact_store.write_artifact(
                session_id=session.id,
                filename="REVIEW.json",
                content=review_artifact_content,
                content_type="application/json",
            )
            review_artifact = PlanningArtifact(
                session_id=session.id,
                artifact_type=PlanningArtifactType.REVIEW_JSON,
                content_url=review_url,
            )
            await store.add_artifact(session.id, review_artifact)
            persisted_artifacts.append(review_artifact)

            self._log(
                session,
                stage=STAGE_PLANNING_ISSUE_WRITER,
                phase="issues",
                event_type="issue_writer_started",
                message_id=queued.message_id,
            )
            if reviewed_issues_json != issues_json:
                await store.append_event(
                    session.id,
                    PlanningEvent(
                        session_id=session.id,
                        event_type="issues_revised",
                        payload={
                            "plan_recommendations_count": review_summary.get(
                                "plan_recommendations_count",
                                0,
                            ),
                            "issue_recommendations_count": review_summary.get(
                                "issue_recommendations_count",
                                0,
                            ),
                            "reviewer_model": review_summary.get("reviewer_model"),
                            "revision_model": review_summary.get("revision_model"),
                        },
                    ),
                )
            created_issues = await self._write_issues_from_payload(
                session=session,
                issues_json=reviewed_issues_json,
                project_slug=session.project_slug,
                github_token=github_token,
            )
            for issue in created_issues:
                issue_payload = {
                    "issue_id": issue.issue_id,
                    "title": issue.title,
                    "url": issue.url,
                    "repo": issue.repo,
                    "number": issue.number,
                }
                issues_created.append(issue_payload)
                self._log(
                    session,
                    stage=STAGE_PLANNING_ISSUE_WRITER,
                    phase="issues",
                    event_type="issue_created",
                    created_issue_id=issue.issue_id,
                    created_issue_url=issue.url,
                    created_issue_number=issue.number,
                    created_issue_repo=issue.repo,
                )
            self._log(
                session,
                stage=STAGE_PLANNING_ISSUE_WRITER,
                phase="issues",
                event_type="issue_writer_done",
                requested_count=len(issues_created),
                created_count=len(issues_created),
                message_id=queued.message_id,
            )
            await store.append_event(
                session.id,
                PlanningEvent(
                    session_id=session.id,
                    event_type="issues_written",
                    payload={
                        "requested_count": len(issues_created),
                        "created": issues_created,
                    },
                ),
            )

            session.status = PLANNING_STATUS_DONE
            session.updated_at = self._utc_now()
            self._log(
                session,
                stage=STAGE_PLANNING_DONE,
                phase="done",
                event_type="done",
                status=PLANNING_STATUS_DONE,
                artifact_count=len(persisted_artifacts),
                issue_created_count=len(issues_created),
                message_id=queued.message_id,
            )
            await store.append_event(
                session.id,
                PlanningEvent(
                    session_id=session.id,
                    event_type="done",
                    payload={
                        "status": PLANNING_STATUS_DONE,
                        "artifacts": [artifact.id for artifact in persisted_artifacts],
                        "issue_created_count": len(issues_created),
                    },
                ),
            )
            await store.update_session(session)
            response_artifacts = [artifact.content_url for artifact in persisted_artifacts]
        except Exception:
            session.status = PLANNING_STATUS_FAILED
            session.updated_at = self._utc_now()
            await store.update_session(session)
            raise

        first_artifact = response_artifacts[0] if response_artifacts else ""
        return {
            "status": PLANNING_STATUS_DONE,
            "session_id": session.id,
            "artifact_urls": response_artifacts,
            "artifact_url": first_artifact,
            "message_id": queued.message_id,
        }
