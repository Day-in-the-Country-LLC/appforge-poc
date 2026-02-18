"""Persistence store tests for planning module."""

from datetime import UTC, datetime, timedelta

import pytest

from ace.planning.models import (
    PlanningArtifact,
    PlanningArtifactType,
    PlanningEvent,
    PlanningSession,
)
from ace.planning.store_firestore import (
    PLANNING_STATUS_EXPIRED,
    PLANNING_STATUS_INTAKE_PENDING,
    PLANNING_STATUS_RUNNING,
    PLANNING_STATUS_TIMED_OUT,
    InMemoryPlanningStore,
)


def _event_types(events: list[PlanningEvent]) -> list[str]:
    return [event.event_type for event in events]


@pytest.mark.asyncio
async def test_in_memory_store_persists_session_and_events() -> None:
    store = InMemoryPlanningStore()
    session = PlanningSession(
        project_slug="example-project",
        request_text="Prepare rollout plan",
        status=PLANNING_STATUS_INTAKE_PENDING,
    )

    await store.create_session(session)
    fetched = await store.get_session(session.id)
    assert fetched is not None
    assert fetched.project_slug == "example-project"

    await store.append_event(
        session.id,
        PlanningEvent(
            session_id=session.id,
            event_type="session_created",
            payload={"status": PLANNING_STATUS_INTAKE_PENDING},
        ),
    )
    events, _ = await store.get_events(session.id)
    assert _event_types(events) == ["session_created"]


@pytest.mark.asyncio
async def test_in_memory_store_sweep_marks_intake_and_running_sessions() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    intake_session = PlanningSession(
        project_slug="example-project",
        request_text="Old intake session",
        status=PLANNING_STATUS_INTAKE_PENDING,
        created_at=now - timedelta(hours=26),
    )
    running_session = PlanningSession(
        project_slug="example-project",
        request_text="Old running session",
        status=PLANNING_STATUS_RUNNING,
        created_at=now - timedelta(minutes=90),
        updated_at=now - timedelta(hours=2),
    )

    store = InMemoryPlanningStore()
    await store.create_session(intake_session)
    await store.create_session(running_session)

    changed = await store.sweep_expired_sessions(
        now=now,
        intake_ttl_hours=24,
        running_ttl_hours=1,
    )
    assert changed == 2

    refreshed_intake = await store.get_session(intake_session.id)
    refreshed_running = await store.get_session(running_session.id)
    assert refreshed_intake.status == PLANNING_STATUS_EXPIRED
    assert refreshed_running.status == PLANNING_STATUS_TIMED_OUT

    intake_events, _ = await store.get_events(intake_session.id)
    assert _event_types(intake_events) == ["session_expired"]
    running_events, _ = await store.get_events(running_session.id)
    assert _event_types(running_events) == ["session_timed_out"]


@pytest.mark.asyncio
async def test_in_memory_store_artifacts_default_to_empty() -> None:
    store = InMemoryPlanningStore()
    session = PlanningSession(
        project_slug="example-project",
        request_text="Session for artifacts",
    )
    await store.create_session(session)
    assert await store.get_artifacts(session.id) == []


@pytest.mark.asyncio
async def test_in_memory_store_add_artifact() -> None:
    store = InMemoryPlanningStore()
    session = PlanningSession(
        project_slug="example-project",
        request_text="Session for artifact writes",
    )
    await store.create_session(session)
    artifact = PlanningArtifact(
        session_id=session.id,
        artifact_type=PlanningArtifactType.PLAN_MARKDOWN,
        content_url="https://example.com/artifacts/PLAN.md",
    )
    await store.add_artifact(session.id, artifact)
    artifacts = await store.get_artifacts(session.id)
    assert len(artifacts) == 1
    assert artifacts[0] == artifact


@pytest.mark.asyncio
async def test_in_memory_store_cursor_behavior() -> None:
    store = InMemoryPlanningStore()
    session = PlanningSession(
        project_slug="example-project",
        request_text="Session for cursor behavior",
    )
    await store.create_session(session)
    for index in range(3):
        await store.append_event(
            session.id,
            PlanningEvent(
                session_id=session.id,
                event_type=f"evt-{index}",
                payload={"index": index},
            ),
        )

    events, next_cursor = await store.get_events(session.id)
    assert [event.event_type for event in events] == ["evt-0", "evt-1", "evt-2"]
    assert next_cursor is not None

    second_page, _ = await store.get_events(session.id, after=events[1].id)
    assert [event.event_type for event in second_page] == ["evt-2"]

    with pytest.raises(Exception):
        await store.get_events(session.id, after="bad")

    assert [artifact.artifact_type for artifact in await store.get_artifacts(session.id)] == []


@pytest.mark.asyncio
async def test_planning_artifact_type_defaults() -> None:
    artifact = PlanningArtifact(
        session_id="plan-session-01",
        artifact_type="plan_md",
        content_url="https://example.com/plan.md",
    )
    assert artifact.session_id == "plan-session-01"
