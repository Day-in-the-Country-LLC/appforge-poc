"""FastAPI service for webhook receiver and health checks."""

import hashlib
import hmac
from typing import Any

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel

from ace.config.logging import configure_logging
from ace.config.settings import get_settings

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

    logger.info(
        "handling_comment_event",
        action=action,
        issue=issue_number,
        comment_length=len(comment_body),
    )

    if action == "created" and "ANSWER:" in comment_body:
        logger.info("answer_received", issue=issue_number)


@app.post("/trigger/poll")
async def trigger_poll(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Manual trigger for polling loop."""
    logger.info("poll_triggered")
    background_tasks.add_task(poll_for_ready_issues)
    return {"status": "polling"}


async def poll_for_ready_issues() -> None:
    """Poll for issues labeled agent:ready."""
    logger.info("polling_for_ready_issues")
