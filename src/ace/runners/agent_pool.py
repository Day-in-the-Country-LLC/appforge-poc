"""Agent pool manager for concurrent issue processing."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import structlog

from ace.config.settings import get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue, IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.github.status_manager import IssueStatus
from ace.orchestration.graph import get_compiled_graph
from ace.orchestration.state import WorkerState

logger = structlog.get_logger(__name__)

MAX_CONCURRENT_AGENTS = 5


class AgentState(str, Enum):
    """State of an agent slot."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentTarget(str, Enum):
    """Target environment for agent execution."""

    LOCAL = "local"  # Issues requiring local machine access
    REMOTE = "remote"  # Issues that can run on cloud VM
    ANY = "any"  # Process any issue regardless of target


@dataclass
class AgentSlot:
    """Represents a slot for running an agent."""

    slot_id: int
    state: AgentState = AgentState.IDLE
    issue: Issue | None = None
    task: asyncio.Task | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


@dataclass
class PoolStatus:
    """Status of the agent pool."""

    total_slots: int
    active_agents: int
    idle_slots: int
    completed_count: int
    failed_count: int
    active_issues: list[int]


class AgentPool:
    """Manages a pool of concurrent coding agents."""

    def __init__(
        self,
        max_agents: int = MAX_CONCURRENT_AGENTS,
        target: AgentTarget = AgentTarget.ANY,
    ):
        """Initialize the agent pool.

        Args:
            max_agents: Maximum number of concurrent agents (default: 5)
            target: Which issues to process (local, remote, or any)
        """
        self.max_agents = max_agents
        self.target = target
        self.slots: list[AgentSlot] = [AgentSlot(slot_id=i) for i in range(max_agents)]
        self.settings = get_settings()
        self._api_client: GitHubAPIClient | None = None
        self._projects_client: ProjectsV2Client | None = None
        self._issue_queue: IssueQueue | None = None
        self._running = False
        self._draining = False  # Drain mode: process until queue empty
        self._completed_count = 0
        self._failed_count = 0
        self._processed_issues: set[int] = set()
        self._session_processed: int = 0  # Issues processed in current session

    @property
    def api_client(self) -> GitHubAPIClient:
        """Get or create the GitHub API client."""
        if self._api_client is None:
            self._api_client = GitHubAPIClient(self.settings.github_token)
        return self._api_client

    @property
    def projects_client(self) -> ProjectsV2Client:
        """Get or create the Projects V2 client."""
        if self._projects_client is None:
            self._projects_client = ProjectsV2Client(self.api_client)
        return self._projects_client

    @property
    def issue_queue(self) -> IssueQueue:
        """Get or create the issue queue."""
        if self._issue_queue is None:
            self._issue_queue = IssueQueue(
                self.api_client,
                self.settings.github_org,
                "",
                self.projects_client,
            )
        return self._issue_queue

    def get_status(self) -> PoolStatus:
        """Get current pool status."""
        active = sum(1 for s in self.slots if s.state == AgentState.RUNNING)
        idle = sum(1 for s in self.slots if s.state == AgentState.IDLE)
        active_issues = [
            s.issue.number for s in self.slots if s.state == AgentState.RUNNING and s.issue
        ]
        return PoolStatus(
            total_slots=self.max_agents,
            active_agents=active,
            idle_slots=idle,
            completed_count=self._completed_count,
            failed_count=self._failed_count,
            active_issues=active_issues,
        )

    def _get_idle_slot(self) -> AgentSlot | None:
        """Get an idle slot if available."""
        for slot in self.slots:
            if slot.state == AgentState.IDLE:
                return slot
        return None

    def _matches_target(self, issue: Issue) -> bool:
        """Check if issue matches the pool's target environment.

        Args:
            issue: The issue to check

        Returns:
            True if issue should be processed by this pool
        """
        if self.target == AgentTarget.ANY:
            return True

        local_label = self.settings.github_local_agent_label
        remote_label = self.settings.github_remote_agent_label

        has_local = local_label in issue.labels
        has_remote = remote_label in issue.labels

        if self.target == AgentTarget.LOCAL:
            # Process if explicitly marked local, or if no target label (backwards compat)
            return has_local or (not has_local and not has_remote)
        elif self.target == AgentTarget.REMOTE:
            # Process if explicitly marked remote, or if no target label (backwards compat)
            return has_remote or (not has_local and not has_remote)

        return True

    async def fetch_ready_issues(self) -> list[Issue]:
        """Fetch issues that are ready for processing.

        Filters out:
        - Already processed issues
        - Issues with open blocking relationships
        - Issues not matching the pool's target environment

        Returns:
            List of issues with "Ready" status and no open blockers
        """
        try:
            issues = await self.issue_queue.list_issues_by_project_status(
                self.settings.github_project_name,
                IssueStatus.READY.value,
            )

            # Filter out already processed issues
            new_issues = [issue for issue in issues if issue.number not in self._processed_issues]

            # Filter by target environment
            target_issues = [issue for issue in new_issues if self._matches_target(issue)]

            # Filter out issues with open blockers
            unblocked_issues = []
            blocked_count = 0

            for issue in target_issues:
                if issue.repo_owner and issue.repo_name:
                    has_blockers = await self.projects_client.has_open_blockers(
                        issue.repo_owner,
                        issue.repo_name,
                        issue.number,
                    )
                    if has_blockers:
                        blocked_count += 1
                        logger.debug(
                            "issue_skipped_has_blockers",
                            issue=issue.number,
                            title=issue.title,
                        )
                        continue
                unblocked_issues.append(issue)

            logger.info(
                "fetched_ready_issues",
                target=self.target.value,
                total=len(issues),
                new=len(new_issues),
                target_matched=len(target_issues),
                blocked=blocked_count,
                unblocked=len(unblocked_issues),
                already_processed=len(self._processed_issues),
            )
            return unblocked_issues

        except Exception as e:
            logger.error("fetch_ready_issues_failed", error=str(e))
            return []

    async def _run_agent_for_issue(self, slot: AgentSlot, issue: Issue) -> None:
        """Run an agent for a specific issue.

        Args:
            slot: The agent slot to use
            issue: The issue to process
        """
        slot.state = AgentState.RUNNING
        slot.issue = issue
        slot.started_at = datetime.now()
        slot.error = None

        logger.info(
            "agent_starting",
            slot=slot.slot_id,
            issue=issue.number,
            title=issue.title,
            repo=f"{issue.repo_owner}/{issue.repo_name}",
        )

        try:
            # Create initial state for the workflow
            initial_state = WorkerState(
                issue=issue,
                issue_number=issue.number,
                agent_id=f"agent-{slot.slot_id}",
                started_at=datetime.now(),
                last_update=datetime.now(),
                metadata={
                    "repo_owner": issue.repo_owner,
                    "repo_name": issue.repo_name,
                    "repo": issue.repo_name,
                },
            )

            # Run the workflow graph
            graph = get_compiled_graph()
            final_state = await graph.ainvoke(initial_state)

            slot.state = AgentState.COMPLETED
            slot.completed_at = datetime.now()
            self._completed_count += 1
            self._session_processed += 1

            logger.info(
                "agent_completed",
                slot=slot.slot_id,
                issue=issue.number,
                pr_number=final_state.pr_number,
                duration_seconds=(slot.completed_at - slot.started_at).total_seconds(),
            )

        except Exception as e:
            slot.state = AgentState.FAILED
            slot.completed_at = datetime.now()
            slot.error = str(e)
            self._failed_count += 1

            logger.error(
                "agent_failed",
                slot=slot.slot_id,
                issue=issue.number,
                error=str(e),
            )

        finally:
            # Mark slot as idle for next issue
            slot.state = AgentState.IDLE
            slot.issue = None
            slot.task = None

    async def spawn_agent(self, issue: Issue) -> bool:
        """Spawn an agent for an issue if a slot is available.

        Args:
            issue: The issue to process

        Returns:
            True if agent was spawned, False if no slots available
        """
        slot = self._get_idle_slot()
        if not slot:
            logger.warning("no_idle_slots_available", issue=issue.number)
            return False

        # Mark as processed to avoid duplicate spawning
        self._processed_issues.add(issue.number)

        # Create and store the task
        slot.task = asyncio.create_task(self._run_agent_for_issue(slot, issue))
        return True

    async def process_ready_issues(self) -> dict[str, Any]:
        """Fetch ready issues and spawn agents for them.

        Returns:
            Summary of processing results
        """
        logger.info("process_ready_issues_starting")

        # Fetch ready issues from project board
        ready_issues = await self.fetch_ready_issues()

        if not ready_issues:
            logger.info("no_ready_issues_found")
            return {
                "status": "no_issues",
                "spawned": 0,
                "skipped": 0,
                "pool_status": self.get_status().__dict__,
            }

        spawned = 0
        skipped = 0

        for issue in ready_issues:
            if self.get_status().idle_slots == 0:
                logger.info(
                    "all_slots_busy", remaining_issues=len(ready_issues) - spawned - skipped
                )
                skipped += len(ready_issues) - spawned - skipped
                break

            if await self.spawn_agent(issue):
                spawned += 1
            else:
                skipped += 1

        logger.info(
            "process_ready_issues_complete",
            spawned=spawned,
            skipped=skipped,
            pool_status=self.get_status().__dict__,
        )

        return {
            "status": "processing",
            "spawned": spawned,
            "skipped": skipped,
            "pool_status": self.get_status().__dict__,
        }

    async def run_continuous(self, poll_interval: int = 60) -> None:
        """Run the agent pool continuously, polling for new issues.

        Args:
            poll_interval: Seconds between polls for new issues
        """
        self._running = True
        logger.info("agent_pool_starting", max_agents=self.max_agents, poll_interval=poll_interval)

        while self._running:
            try:
                await self.process_ready_issues()
            except Exception as e:
                logger.error("pool_cycle_error", error=str(e))

            # Wait before next poll
            await asyncio.sleep(poll_interval)

        logger.info("agent_pool_stopped")

    async def run_until_empty(self, check_interval: int = 30) -> dict[str, Any]:
        """Process all unblocked issues until none remain.

        This is the "drain" mode - keeps running until:
        - No ready issues with open blockers
        - All agents are idle

        Args:
            check_interval: Seconds between checks for completed agents

        Returns:
            Summary of the session
        """
        self._draining = True
        self._session_processed = 0
        session_start = datetime.now()

        logger.info("drain_mode_starting", max_agents=self.max_agents)

        while self._draining:
            # Fetch and spawn for any ready issues
            result = await self.process_ready_issues()

            status = self.get_status()

            # Check if we're done:
            # - No issues were spawned or available
            # - All agents are idle (no active work)
            if result["spawned"] == 0 and status.active_agents == 0:
                # Double-check by fetching again (in case blockers just resolved)
                ready_issues = await self.fetch_ready_issues()
                if not ready_issues:
                    logger.info(
                        "drain_mode_complete",
                        session_processed=self._session_processed,
                        duration_seconds=(datetime.now() - session_start).total_seconds(),
                    )
                    break

            # Wait before next check
            await asyncio.sleep(check_interval)

        self._draining = False

        return {
            "status": "complete",
            "session_processed": self._session_processed,
            "duration_seconds": (datetime.now() - session_start).total_seconds(),
            "completed_count": self._completed_count,
            "failed_count": self._failed_count,
        }

    def stop(self) -> None:
        """Stop the continuous polling loop."""
        self._running = False
        self._draining = False
        logger.info("agent_pool_stop_requested")

    async def wait_for_completion(self, timeout: float | None = None) -> None:
        """Wait for all active agents to complete.

        Args:
            timeout: Maximum seconds to wait (None = wait forever)
        """
        tasks = [slot.task for slot in self.slots if slot.task and not slot.task.done()]
        if tasks:
            logger.info("waiting_for_agents", count=len(tasks))
            await asyncio.wait(tasks, timeout=timeout)

    async def shutdown(self) -> None:
        """Gracefully shutdown the agent pool."""
        self.stop()
        await self.wait_for_completion(timeout=30)
        if self._api_client:
            await self._api_client.close()
        logger.info("agent_pool_shutdown_complete")


# Global pool instances (one per target)
_pools: dict[AgentTarget, AgentPool] = {}


def get_pool(target: AgentTarget = AgentTarget.ANY) -> AgentPool:
    """Get or create an agent pool for the specified target.

    Args:
        target: Which issues to process (local, remote, or any)

    Returns:
        AgentPool instance for the specified target
    """
    global _pools
    if target not in _pools:
        _pools[target] = AgentPool(target=target)
    return _pools[target]
