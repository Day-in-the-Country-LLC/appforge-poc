"""Run a single issue end-to-end using the orchestration graph."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import os

import structlog

from ace.config.logging import configure_logging
from ace.config.secrets import resolve_github_token
from ace.config.settings import get_settings, set_settings_overrides
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import IssueQueue
from ace.github.projects_v2 import ProjectsV2Client
from ace.github.status_manager import IssueStatus
from ace.orchestration.graph import get_compiled_graph
from ace.agents.base import AgentStatus
from ace.orchestration.state import WorkerState

logger = structlog.get_logger(__name__)


def _matches_target(labels: list[str], settings, target: str) -> bool:
    target = target.lower()
    if target == "remote":
        return settings.github_remote_agent_label in labels
    if target == "local":
        return (
            settings.github_local_agent_label in labels
            or settings.github_remote_agent_label in labels
        )
    return False


async def _select_unblocked_issue(
    settings,
    api_client: GitHubAPIClient,
    target: str,
) -> tuple[str, str, int] | None:
    projects_client = ProjectsV2Client(api_client)
    project_id = await projects_client.get_org_project_id(
        settings.github_org, settings.github_project_name
    )
    if not project_id:
        logger.error("harness_project_not_found", project=settings.github_project_name)
        return None

    status = settings.github_ready_status or IssueStatus.READY.value
    items = await projects_client.list_project_items_by_status(project_id, status)
    for item in items:
        if item.content_type != "Issue":
            continue
        if not _matches_target(item.labels, settings, target):
            continue
        has_blockers = await projects_client.has_open_blockers(
            item.repo_owner,
            item.repo_name,
            item.number,
        )
        if has_blockers:
            continue
        return item.repo_owner, item.repo_name, item.number
    return None


def _parse_repo_issue(value: str) -> tuple[str, int] | None:
    """Parse strings like 'repo-123' or 'repo#123' into (repo, number)."""
    candidate = value.strip()
    if not candidate:
        return None
    for sep in ("-", "#"):
        if sep in candidate:
            repo, num = candidate.rsplit(sep, 1)
            if repo and num.isdigit():
                return repo, int(num)
    return None


async def run_issue(
    owner: str | None,
    repo: str | None,
    issue_number: int | None,
    auto: bool,
    target: str,
) -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug)

    token = resolve_github_token(settings)
    api_client = GitHubAPIClient(token)

    if auto:
        selection = await _select_unblocked_issue(settings, api_client, target)
        if not selection:
            logger.warning("harness_no_unblocked_issues", target=target)
            await api_client.close()
            return
        owner, repo, issue_number = selection

    if not owner or not repo or issue_number is None:
        raise ValueError("owner/repo/issue required unless --auto is set")

    issue_queue = IssueQueue(api_client, owner, repo)
    issue = await issue_queue.get_issue(issue_number, repo_owner=owner, repo_name=repo)

    initial_state = WorkerState(
        issue=issue,
        issue_number=issue.number,
        agent_id=settings.agent_id,
        started_at=datetime.now(),
        last_update=datetime.now(),
        metadata={
            "repo_owner": owner,
            "repo_name": repo,
            "repo": repo,
        },
    )

    logger.info(
        "harness_starting",
        issue=issue.number,
        repo=f"{owner}/{repo}",
        title=issue.title,
    )

    graph = get_compiled_graph()
    final_state = await graph.ainvoke(initial_state)

    # Handle dict or object state
    agent_result = None
    if hasattr(final_state, "agent_result"):
        agent_result = final_state.agent_result
    elif isinstance(final_state, dict):
        agent_result = final_state.get("agent_result")
        # If agent_result was serialized as a dict, keep it as-is
    if agent_result is None:
        raise RuntimeError("Instructions were not generated or agent did not run.")
    status_val = getattr(agent_result, "status", None)
    if isinstance(agent_result, dict):
        status_val = agent_result.get("status")
    if status_val != AgentStatus.SUCCESS:
        err = (
            getattr(agent_result, "error", None)
            or getattr(agent_result, "output", "")
            or (agent_result.get("error") if isinstance(agent_result, dict) else "")
        )
        raise RuntimeError(f"Agent failed before completion: {err}")
    logger.info(
        "harness_complete",
        issue=issue.number,
        pr_number=getattr(final_state, "pr_number", None),
        status="success",
    )

    await api_client.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a single issue end-to-end against GitHub (creates comments/PRs)."
    )
    parser.add_argument("--owner", help="GitHub repo owner/org")
    parser.add_argument("--repo", help="GitHub repository name")
    parser.add_argument("--issue", type=int, help="Issue number")
    parser.add_argument(
        "--dev",
        help="Convenience flag: provide <repo>-<issue> (owner defaults to configured org).",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Pick the first unblocked issue in the configured project",
    )
    parser.add_argument(
        "--target",
        choices=["remote", "local"],
        default="remote",
        help="Filter auto-selected issues by target label",
    )
    parser.add_argument(
        "--secrets-backend",
        choices=["secret-manager", "env"],
        default="secret-manager",
        help="Where to load secrets from (default: secret-manager).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    set_settings_overrides(secrets_backend=args.secrets_backend)
    settings = get_settings()

    owner = args.owner
    repo = args.repo
    issue_number = args.issue
    auto = args.auto

    if args.dev:
        parsed = _parse_repo_issue(args.dev)
        if not parsed:
            raise SystemExit("--dev must be in the form <repo>-<issue>")
        repo, issue_number = parsed
        owner = owner or settings.github_org
        auto = False
        # Dev mode: do not touch issue comments or status.
        os.environ["DISABLE_ISSUE_COMMENTS"] = "true"
        os.environ["DISABLE_ISSUE_STATUS"] = "true"

    asyncio.run(run_issue(owner, repo, issue_number, auto, args.target))


if __name__ == "__main__":
    main()
