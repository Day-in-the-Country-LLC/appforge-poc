"""FastAPI service for webhook receiver and health checks."""

import hashlib
import hmac
from typing import Any

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ace.config.logging import configure_logging
from ace.config.settings import get_settings
from ace.metrics import metrics
from ace.runners.agent_pool import AgentTarget, get_pool
from ace.runners.scheduler import get_scheduler

logger = structlog.get_logger(__name__)

app = FastAPI(title="Agentic Coding Engine", version="0.1.0")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class WebhookPayload(BaseModel):
    """GitHub webhook payload."""

    action: str
    issue: dict[str, Any] | None = None
    pull_request: dict[str, Any] | None = None


@app.on_event("startup")
async def startup_event():
    """Initialize on startup."""
    settings = get_settings()
    configure_logging(debug=settings.debug)
    logger.info("service_starting", environment=settings.environment)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="healthy", version="0.1.0")


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint() -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain")


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """GitHub webhook receiver.

    Validates webhook signature and queues work.
    """
    settings = get_settings()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature:
        logger.warning("webhook_missing_signature")
        raise HTTPException(status_code=401, detail="Missing signature")

    body = await request.body()

    if settings.github_webhook_secret:
        expected_signature = (
            "sha256="
            + hmac.new(
                settings.github_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )

        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("webhook_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    logger.info("webhook_received", action=payload.get("action"))

    event_type = request.headers.get("X-GitHub-Event", "unknown")

    if event_type == "issues":
        background_tasks.add_task(handle_issue_event, payload)
    elif event_type == "issue_comment":
        background_tasks.add_task(handle_comment_event, payload)

    return {"status": "queued"}


async def handle_issue_event(payload: dict[str, Any]) -> None:
    """Handle GitHub issue event."""
    action = payload.get("action")
    issue = payload.get("issue", {})
    issue_number = issue.get("number")
    labels = [label["name"] for label in issue.get("labels", [])]

    logger.info("handling_issue_event", action=action, issue=issue_number, labels=labels)

    if action == "labeled" and "agent:ready" in labels:
        logger.info("issue_ready_for_agent", issue=issue_number)


async def handle_comment_event(payload: dict[str, Any]) -> None:
    """Handle GitHub issue comment event."""
    action = payload.get("action")
    issue = payload.get("issue", {})
    comment = payload.get("comment", {})
    issue_number = issue.get("number")
    comment_body = comment.get("body", "")
    repo = payload.get("repository", {}) or {}
    repo_owner = (repo.get("owner") or {}).get("login")
    repo_name = repo.get("name")

    logger.info(
        "handling_comment_event",
        action=action,
        issue=issue_number,
        comment_length=len(comment_body),
    )

    if action == "created" and comment_body.strip().startswith("BLOCKED:"):
        try:
            settings = get_settings()
            api_client = GitHubAPIClient(resolve_github_token(settings))
            projects_client = ProjectsV2Client(api_client)
            issue_queue = IssueQueue(
                api_client,
                settings.github_org,
                "",
                projects_client,
            )
            status_manager = StatusManager(issue_queue)
            await status_manager.mark_blocked_from_comment(
                issue_number,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
            await api_client.close()
        except Exception as e:
            logger.error("mark_blocked_from_comment_failed", issue=issue_number, error=str(e))

    if action == "created" and "ANSWER:" in comment_body:
        logger.info("answer_received", issue=issue_number)


@app.post("/trigger/poll")
async def trigger_poll(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Manual trigger for polling loop."""
    logger.info("poll_triggered")
    background_tasks.add_task(poll_for_ready_issues)
    return {"status": "polling"}


async def poll_for_ready_issues(target: str) -> None:
    """Poll for issues labeled agent:ready."""
    logger.info("polling_for_ready_issues", target=target)


@app.get("/agents/status")
async def agent_status(target: str = "any") -> dict[str, Any]:
    """Get current agent pool status.

    Args:
        target: Which pool to check (local, remote, or any)
    """
    agent_target = AgentTarget(target)
    pool = get_pool(agent_target)
    status = pool.get_status()
    return {
        "target": target,
        "total_slots": status.total_slots,
        "active_agents": status.active_agents,
        "idle_slots": status.idle_slots,
        "completed_count": status.completed_count,
        "failed_count": status.failed_count,
    }


@app.post("/agents/spawn")
async def spawn_agents(
    background_tasks: BackgroundTasks,
    target: str = "any",
) -> dict[str, Any]:
    """Spawn agents for all ready issues (up to max concurrent).

    Fetches issues with 'Ready' status from the project board and
    spawns up to 5 concurrent agents to process them.

    Args:
        target: Which issues to process (local, remote, or any)
    """
    agent_target = AgentTarget(target)
    logger.info("spawn_agents_triggered", target=target)
    pool = get_pool(agent_target)

    # Run in background so endpoint returns immediately
    background_tasks.add_task(pool.process_ready_issues)

    status = pool.get_status()
    return {
        "status": "spawning",
        "message": f"Spawning agents for ready issues. {status.idle_slots} slots available.",
        "pool_status": {
            "active_agents": status.active_agents,
            "idle_slots": status.idle_slots,
        },
    }


@app.post("/agents/run")
async def run_until_empty(
    background_tasks: BackgroundTasks,
    target: str = "remote",
) -> dict[str, Any]:
    """Run agents until all unblocked issues are processed.

    This is the recommended daily trigger endpoint. It will:
    1. Fetch all 'Ready' issues from the project board
    2. Filter by target (local/remote) and open blockers
    3. Spawn up to 5 concurrent agents
    4. Continue checking and spawning until no unblocked issues remain
    5. Stop automatically when done

    Args:
        target: Which issues to process (local, remote, or any). Default: remote

    Ideal for morning scheduled runs via Cloud Scheduler.
    """
    agent_target = AgentTarget(target)
    logger.info("run_until_empty_triggered", target=target)
    pool = get_pool(agent_target)

    # Check if already running
    status = pool.get_status()
    if status.active_agents > 0:
        return {
            "status": "already_running",
            "message": f"{status.active_agents} agents already active",
            "pool_status": {
                "active_agents": status.active_agents,
                "idle_slots": status.idle_slots,
            },
        }

    # Run drain mode in background
    background_tasks.add_task(pool.run_until_empty)

    return {
        "status": "started",
        "message": "Processing all unblocked issues until queue is empty",
    }


@app.post("/agents/start")
async def start_continuous_processing(
    background_tasks: BackgroundTasks,
    target: str = "any",
) -> dict[str, str]:
    """Start continuous agent pool processing.

    The pool will poll for ready issues every 60 seconds and
    spawn agents as slots become available. Runs indefinitely.

    Args:
        target: Which issues to process (local, remote, or any)
    """
    agent_target = AgentTarget(target)
    logger.info("continuous_processing_started", target=target)
    pool = get_pool(agent_target)
    settings = get_settings()

    background_tasks.add_task(
        pool.run_continuous,
        poll_interval=settings.polling_interval_seconds,
    )

    return {"status": "started", "message": "Agent pool running continuously"}


@app.post("/agents/stop")
async def stop_continuous_processing(target: str = "any") -> dict[str, str]:
    """Stop continuous or drain mode processing.

    Args:
        target: Which pool to stop (local, remote, or any)
    """
    agent_target = AgentTarget(target)
    logger.info("processing_stopped", target=target)
    pool = get_pool(agent_target)
    pool.stop()
    return {"status": "stopped", "target": target, "message": "Agent pool stop requested"}


@app.get("/scheduler/status")
async def scheduler_status() -> dict[str, Any]:
    """Get daily scheduler status."""
    scheduler = get_scheduler()
    return scheduler.get_status()


@app.post("/scheduler/start")
async def start_scheduler(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Start the daily scheduler.

    The scheduler will trigger agent runs at 8:00 AM Eastern daily.
    Each run processes all unblocked issues until the queue is empty.
    """
    logger.info("daily_scheduler_start_requested")
    scheduler = get_scheduler()

    if scheduler._running:
        return {
            "status": "already_running",
            "message": "Daily scheduler is already running",
            **scheduler.get_status(),
        }

    background_tasks.add_task(scheduler.run_daily)

    return {
        "status": "started",
        "message": "Daily scheduler started",
        **scheduler.get_status(),
    }


@app.post("/scheduler/stop")
async def stop_scheduler() -> dict[str, str]:
    """Stop the daily scheduler."""
    logger.info("daily_scheduler_stop_requested")
    scheduler = get_scheduler()
    scheduler.stop()
    return {"status": "stopped", "message": "Daily scheduler stopped"}
