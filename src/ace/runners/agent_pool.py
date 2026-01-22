"""Agent pool manager for concurrent issue processing."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from pathlib import Path
import time

import structlog

from ace.config.settings import get_settings
from ace.config.secrets import resolve_github_token
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import Issue, IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.github.status_manager import IssueStatus
from ace.metrics import metrics
from ace.orchestration.graph import get_compiled_graph
from ace.orchestration.state import WorkerState
from ace.workspaces.git_ops import GitOps
from ace.workspaces.tmux_ops import (
    TmuxOps,
    parse_issue_from_session,
    session_name_for_issue,
)
from fastmcp import Client as McpClient
from ace.agents.manager_agent import ManagerAgent

logger = structlog.get_logger(__name__)

MAX_CONCURRENT_AGENTS = 5


def _extract_mcp_items(resp: Any) -> list[dict[str, Any]]:
    """Normalize MCP tool responses into a list of issue-like dicts."""
    if resp is None:
        return []

    for attr in ("structured_content", "structuredContent"):
        structured = getattr(resp, attr, None)
        if isinstance(structured, dict) and isinstance(structured.get("result"), list):
            return structured["result"]

    content = getattr(resp, "content", None)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    parsed = json.loads(part.get("text", "[]"))
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    return parsed
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed

    if isinstance(resp, dict):
        if isinstance(resp.get("result"), list):
            return resp["result"]
        structured = resp.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("result"), list):
            return structured["result"]
        content = resp.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    try:
                        parsed = json.loads(part.get("text", "[]"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, list):
                        return parsed
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed

    if isinstance(resp, list):
        return resp

    return []


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
        max_issues_per_run: int = 0,
    ):
        """Initialize the agent pool.

        Args:
            max_agents: Maximum number of concurrent agents (default: 5)
            target: Which issues to process (local, remote, or any)
            max_issues_per_run: Limit issues processed per run (0 = unlimited)
        """
        self.max_agents = max_agents
        self.target = target
        self.slots: list[AgentSlot] = [AgentSlot(slot_id=i) for i in range(max_agents)]
        self.settings = get_settings()
        self._api_client: GitHubAPIClient | None = None
        self._projects_client: ProjectsV2Client | None = None
        self._issue_queue: IssueQueue | None = None
        self._manager_agent: ManagerAgent | None = None
        self._project_id: str | None = None
        self._running = False
        self._draining = False  # Drain mode: process until queue empty
        self._completed_count = 0
        self._failed_count = 0
        self._processed_issues: set[int] = set()
        self._session_processed: int = 0  # Issues processed in current session
        self._resume_completed: bool = False
        self._last_cleanup_at: datetime | None = None
        self.max_issues_per_run = max_issues_per_run

    @property
    def api_client(self) -> GitHubAPIClient:
        """Get or create the GitHub API client."""
        if self._api_client is None:
            token = resolve_github_token(self.settings)
            self._api_client = GitHubAPIClient(token)
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

    def _get_manager_agent(self) -> ManagerAgent | None:
        if not self.settings.manager_agent_enabled:
            return None
        if self._manager_agent is None:
            self._manager_agent = ManagerAgent()
        return self._manager_agent

    async def _hydrate_issue(self, issue: Issue) -> Issue:
        if not issue.repo_owner or not issue.repo_name:
            return issue
        try:
            full = await self.issue_queue.get_issue(
                issue.number,
                repo_owner=issue.repo_owner,
                repo_name=issue.repo_name,
            )
            full.repo_owner = issue.repo_owner
            full.repo_name = issue.repo_name
            return full
        except Exception as exc:
            logger.warning(
                "issue_hydration_failed",
                issue=issue.number,
                error=str(exc),
            )
            return issue

    async def _hydrate_issues(self, issues: list[Issue]) -> list[Issue]:
        hydrated: list[Issue] = []
        for issue in issues:
            hydrated.append(await self._hydrate_issue(issue))
        return hydrated

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

    async def _get_project_id(self) -> str:
        if self._project_id:
            return self._project_id
        project_id = await self.projects_client.get_org_project_id(
            self.settings.github_org,
            self.settings.github_project_name,
        )
        if not project_id:
            raise ValueError(
                f"Project '{self.settings.github_project_name}' not found in org "
                f"'{self.settings.github_org}'"
            )
        self._project_id = project_id
        return project_id

    async def _has_blockers_not_done(self, issue: Issue) -> bool:
        if not issue.repo_owner or not issue.repo_name:
            return False
        blockers = await self.projects_client.get_issue_blockers(
            issue.repo_owner,
            issue.repo_name,
            issue.number,
        )
        if not blockers:
            return False
        project_id = await self._get_project_id()
        not_done = []
        for blocker in blockers:
            status = await self.projects_client.get_issue_project_status(
                project_id,
                blocker.number,
                blocker.repo_owner,
                blocker.repo_name,
            )
            if status != IssueStatus.DONE.value:
                not_done.append((blocker.number, status))
        if not_done:
            logger.debug(
                "issue_skipped_blockers_not_done",
                issue=issue.number,
                blockers=not_done,
            )
            return True
        return False

    def set_max_issues_per_run(self, limit: int) -> None:
        """Set the maximum issues to process in this run (0 = unlimited)."""
        self.max_issues_per_run = max(0, limit)
        logger.info("max_issues_per_run_set", limit=self.max_issues_per_run, target=self.target.value)

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
            # Process only if explicitly marked local
            return has_local
        elif self.target == AgentTarget.REMOTE:
            # Process only if explicitly marked remote
            return has_remote

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
        # Prefer appforge MCP server when enabled and target is remote/any (server filters remote label + blockers)
        if self.settings.appforge_mcp_enabled and self.target in {AgentTarget.REMOTE, AgentTarget.ANY}:
            mcp_issues = await self._fetch_ready_issues_via_mcp()
            if mcp_issues:
                issues = mcp_issues
            else:
                issues = []
        else:
            issues = []

        try:
            if not issues:
                issues = await self.issue_queue.list_issues_by_project_status(
                    self.settings.github_project_name,
                    IssueStatus.READY.value,
                )

            # Filter out already processed issues
            new_issues = [issue for issue in issues if issue.number not in self._processed_issues]

            # Filter by target environment
            target_issues = [issue for issue in new_issues if self._matches_target(issue)]

            # Filter out issues with blockers not in Done status
            unblocked_issues = []
            blocked_count = 0

            for issue in target_issues:
                if await self._has_blockers_not_done(issue):
                    blocked_count += 1
                    continue
                unblocked_issues.append(issue)

            unblocked_issues = await self._hydrate_issues(unblocked_issues)
            manager = self._get_manager_agent()
            if manager:
                selected = await manager.select_ready_issues(unblocked_issues)
                unblocked_issues = [issue for issue in unblocked_issues if issue.number in selected]

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

    async def _fetch_ready_issues_via_mcp(self) -> list[Issue]:
        """Fetch ready issues via appforge MCP server (already filtered by status/label/blockers)."""
        url = self.settings.appforge_mcp_url.rstrip("/")
        if not url.endswith("/mcp"):
            url = f"{url}/mcp"

        try:
            async with McpClient(url) as client:
                args = {
                    "project_name": self.settings.github_project_name,
                    "status": self.settings.github_ready_status,
                    "remote_label": self.settings.github_remote_agent_label,
                }
                resp = await client.call_tool("list_ready_remote_items", args)
                issues: list[Issue] = []
                now = datetime.now(UTC)
                for item in _extract_mcp_items(resp):
                    try:
                        issues.append(
                            Issue(
                                number=int(item["number"]),
                                title=item.get("title", ""),
                                body="",
                                labels=item.get("labels", []),
                                assignee=None,
                                state="open",
                                created_at=now,
                                updated_at=now,
                                html_url=item.get("html_url", ""),
                                repo_owner=item.get("repo_owner"),
                                repo_name=item.get("repo_name"),
                            )
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("mcp_issue_parse_failed", item=item, error=str(exc))
                logger.info(
                    "fetched_ready_issues_via_mcp",
                    count=len(issues),
                    target=self.target.value,
                )
                return issues
        except Exception as exc:
            logger.warning("fetch_ready_issues_via_mcp_failed", error=str(exc))
            return []

    async def fetch_in_progress_issues(self) -> list[Issue]:
        """Fetch issues already in progress for resume sweep."""
        try:
            issues = await self.issue_queue.list_issues_by_project_status(
                self.settings.github_project_name,
                IssueStatus.IN_PROGRESS.value,
            )

            issues = await self._hydrate_issues(issues)
            filtered = []
            for issue in issues:
                if issue.number in self._processed_issues:
                    continue
                if not self._matches_target(issue):
                    continue
                if self.settings.github_agent_label not in issue.labels:
                    continue
                if issue.assignee:
                    logger.debug(
                        "issue_skipped_assigned",
                        issue=issue.number,
                        assignee=issue.assignee,
                    )
                    continue
                filtered.append(issue)

            manager = self._get_manager_agent()
            if manager:
                selected = await manager.select_resume_issues(filtered)
                filtered = [issue for issue in filtered if issue.number in selected]

            logger.info(
                "fetched_in_progress_issues",
                total=len(issues),
                filtered=len(filtered),
            )
            return filtered
        except Exception as e:
            logger.error("fetch_in_progress_issues_failed", error=str(e))
            return []

    async def resume_in_progress_issues(self) -> dict[str, Any]:
        """Resume issues that were in progress (startup sweep)."""
        if not self.settings.resume_in_progress_issues or self._resume_completed:
            return {"status": "skipped", "spawned": 0}

        in_progress = await self.fetch_in_progress_issues()
        if not in_progress:
            self._resume_completed = True
            return {"status": "none", "spawned": 0}

        spawned = 0
        skipped = 0
        for issue in in_progress:
            if self.get_status().idle_slots == 0:
                skipped += len(in_progress) - spawned - skipped
                break
            if await self.spawn_agent(issue):
                spawned += 1
            else:
                skipped += 1

        self._resume_completed = True
        logger.info(
            "resume_in_progress_complete",
            spawned=spawned,
            skipped=skipped,
        )
        return {"status": "resumed", "spawned": spawned, "skipped": skipped}

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
        metrics.inc_counter(
            "ace_agent_runs_total",
            labels={"status": "started", "backend": "unknown"},
        )
        metrics.inc_gauge("ace_active_agents", 1)

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

            # Handle both dict and WorkerState return types from graph
            agent_result = None
            if isinstance(final_state, dict):
                pr_number = final_state.get("pr_number")
                backend = final_state.get("backend") or "unknown"
                agent_result = final_state.get("agent_result")
            else:
                pr_number = final_state.pr_number
                backend = final_state.backend or "unknown"
                agent_result = final_state.agent_result

            result_status = "success"
            if agent_result:
                if isinstance(agent_result, dict):
                    status_value = agent_result.get("status")
                else:
                    status_value = agent_result.status.value
                if status_value != "success":
                    result_status = "failed"

            duration_seconds = (slot.completed_at - slot.started_at).total_seconds()
            metrics.observe_summary(
                "ace_agent_duration_seconds",
                duration_seconds,
                labels={"backend": backend},
            )
            metrics.inc_counter(
                "ace_agent_runs_total",
                labels={"status": result_status, "backend": backend},
            )

            logger.info(
                "agent_completed",
                slot=slot.slot_id,
                issue=issue.number,
                pr_number=pr_number,
                duration_seconds=duration_seconds,
            )

        except Exception as e:
            slot.state = AgentState.FAILED
            slot.completed_at = datetime.now()
            slot.error = str(e)
            self._failed_count += 1
            duration_seconds = (slot.completed_at - slot.started_at).total_seconds()
            metrics.observe_summary(
                "ace_agent_duration_seconds",
                duration_seconds,
                labels={"backend": "unknown"},
            )
            metrics.inc_counter(
                "ace_agent_runs_total",
                labels={"status": "failed", "backend": "unknown"},
            )

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
            metrics.dec_gauge("ace_active_agents", 1)

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

        # Reserve the slot immediately to avoid oversubscribing slots.
        slot.state = AgentState.RUNNING
        slot.issue = issue

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

        limit = self.max_issues_per_run
        if limit > 0:
            remaining = limit - self._session_processed
            if remaining <= 0:
                logger.info("max_issues_reached", max_issues=limit)
                return {
                    "status": "max_reached",
                    "spawned": 0,
                    "skipped": 0,
                    "pool_status": self.get_status().__dict__,
                }

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

        if limit > 0:
            remaining = limit - self._session_processed
            ready_issues = ready_issues[:remaining]

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
        self._session_processed = 0
        logger.info("agent_pool_starting", max_agents=self.max_agents, poll_interval=poll_interval)

        if self.settings.resume_in_progress_issues:
            try:
                await self.resume_in_progress_issues()
            except Exception as e:
                logger.error("resume_in_progress_failed", error=str(e))

        while self._running:
            try:
                await self.process_ready_issues()
            except Exception as e:
                logger.error("pool_cycle_error", error=str(e))

            await self._maybe_cleanup()

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

            if result.get("status") == "max_reached":
                logger.info(
                    "drain_mode_max_issues_reached",
                    session_processed=self._session_processed,
                )
                break

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
            await self._maybe_cleanup()
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

    async def _maybe_cleanup(self) -> None:
        if not self.settings.cleanup_enabled:
            return
        now = datetime.utcnow()
        if self._last_cleanup_at:
            elapsed = (now - self._last_cleanup_at).total_seconds()
            if elapsed < self.settings.cleanup_interval_seconds:
                return
        self._last_cleanup_at = now
        await self._cleanup_stale_resources()

    async def _cleanup_stale_resources(self) -> None:
        worktrees_root = Path(self.settings.agent_workspace_root) / "worktrees"
        if not worktrees_root.exists():
            return

        tmux = TmuxOps()
        git_ops = GitOps(self.settings.agent_workspace_root)
        active_issues = {
            slot.issue.number
            for slot in self.slots
            if slot.state == AgentState.RUNNING and slot.issue
        }
        active_sessions = {
            session_name_for_issue(slot.issue.repo_name, slot.issue.number)
            for slot in self.slots
            if slot.state == AgentState.RUNNING and slot.issue
        }

        retention = timedelta(hours=self.settings.cleanup_worktree_retention_hours)
        now = datetime.utcnow()

        for repo_dir in worktrees_root.iterdir():
            if not repo_dir.is_dir():
                continue
            for issue_dir in repo_dir.iterdir():
                if not issue_dir.is_dir() or not issue_dir.name.isdigit():
                    continue

                issue_number = int(issue_dir.name)
                if issue_number in active_issues:
                    continue

                session_name = session_name_for_issue(repo_dir.name, issue_number)
                if tmux.session_exists(session_name):
                    continue
                if self.settings.cleanup_only_done:
                    # Without task status, skip cleanup when only_done is enforced
                    continue

                last_activity = issue_dir.stat().st_mtime
                tasks_path = issue_dir / "ace_tasks.json"
                if tasks_path.exists():
                    last_activity = max(last_activity, tasks_path.stat().st_mtime)

                age = now - datetime.utcfromtimestamp(last_activity)
                if age < retention:
                    continue

                logger.info(
                    "cleanup_worktree",
                    repo=repo_dir.name,
                    issue=issue_number,
                    age_hours=round(age.total_seconds() / 3600, 2),
                )
                await git_ops.cleanup_worktree(issue_dir)

        if not self.settings.cleanup_tmux_enabled:
            return

        tmux_retention_seconds = self.settings.cleanup_tmux_retention_hours * 3600
        now_ts = time.time()
        for session_name, activity_epoch in tmux.list_sessions():
            if session_name in active_sessions:
                continue
            if now_ts - activity_epoch < tmux_retention_seconds:
                continue

            parsed = parse_issue_from_session(session_name)
            if parsed:
                repo_slug, issue_number = parsed
                worktree_path = worktrees_root / repo_slug / str(issue_number)
                if worktree_path.exists() and self.settings.cleanup_only_done:
                    continue

            logger.info(
                "cleanup_tmux_session",
                session=session_name,
            )
            tmux.kill_session(session_name)

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
