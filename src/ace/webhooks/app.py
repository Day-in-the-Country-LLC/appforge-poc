"""FastAPI webhook listener for GitHub events."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request

from ace.config.logging import configure_logging
from ace.config.settings import Settings, get_settings
from ace.github.api_client import GitHubAPIClient
from ace.github.issue_queue import IssueQueue
from ace.notifications.slack_client import (
    SlackNotifier,
    format_error_message,
    format_webhook_message,
)
from ace.planning.routes import planning_router, planning_worker_router
from ace.webhooks.handlers import WebhookHandler
from ace.webhooks.lifecycle import (
    STAGE_AGENT_FINISHED,
    STAGE_AGENT_STARTED,
    STAGE_FINAL_RESOLUTION,
    STAGE_WEBHOOK_ENQUEUED,
    STAGE_WEBHOOK_RECEIVED,
    STAGE_WORKER_DEQUEUED,
    STAGE_WORKER_STARTED,
    build_lifecycle_context,
    log_lifecycle_event,
    normalize_error_resolution,
    normalize_result_resolution,
)
from ace.pr_review.job_store import build_pr_review_session_store
from ace.pr_review.pubsub_queue import decode_pubsub_push as decode_pr_review_job
from ace.pr_review.runtime import PRReviewRuntime
from ace.webhooks.pubsub_queue import PubSubWebhookQueue, decode_pubsub_push
from ace.webhooks.repo_gcp_mapping import load_repo_gcp_mapping

logger = structlog.get_logger(__name__)

app = FastAPI()
_handler: WebhookHandler | None = None
_notifier: SlackNotifier | None = None
_queue: PubSubWebhookQueue | None = None
_settings: Settings | None = None
_repo_gcp_mapping: dict[str, str] | None = None
_pr_review_session_store = None
_pr_review_app_auth: object | None = None
app.include_router(planning_router)
app.include_router(planning_worker_router)


@app.on_event("startup")
async def _startup() -> None:
    settings = _get_settings()
    configure_logging(debug=settings.debug)
    _validate_logging_configuration()
    _get_repo_gcp_mapping()
    global _notifier
    _notifier = SlackNotifier.from_settings(settings)
    logger.info("webhook_listener_started", role=settings.webhook_service_role)


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def _listener_enabled() -> bool:
    role = (_get_settings().webhook_service_role or "").strip().lower()
    return role in ("listener", "both")


def _worker_enabled() -> bool:
    role = (_get_settings().webhook_service_role or "").strip().lower()
    return role in ("worker", "both")


def _get_queue() -> PubSubWebhookQueue:
    global _queue
    if _queue is not None:
        return _queue
    _queue = PubSubWebhookQueue.from_settings(_get_settings())
    return _queue


def _get_repo_gcp_mapping() -> dict[str, str]:
    global _repo_gcp_mapping
    if _repo_gcp_mapping is not None:
        return _repo_gcp_mapping
    settings = _get_settings()
    path = getattr(settings, "repo_gcp_mapping_path", "docs/repo-gcp-mapping.json")
    _repo_gcp_mapping = load_repo_gcp_mapping(path)
    return _repo_gcp_mapping


def _get_pr_review_session_store():
    global _pr_review_session_store
    if _pr_review_session_store is None:
        _pr_review_session_store = build_pr_review_session_store(_get_settings())
    return _pr_review_session_store


def _get_pr_review_app_auth() -> object:
    global _pr_review_app_auth
    if _pr_review_app_auth is None:
        _pr_review_app_auth = _get_handler().app_auth
    return _pr_review_app_auth


def _get_handler() -> WebhookHandler:
    global _handler
    if _handler is not None:
        return _handler
    settings = _get_settings()
    if not settings.appforge_mcp_url:
        raise RuntimeError("❌ ERROR: APPFORGE_MCP_URL is required for worker mode")
    _handler = WebhookHandler()
    return _handler


def _verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not secret:
        raise HTTPException(status_code=500, detail="❌ ERROR: GITHUB_WEBHOOK_SECRET not set")
    if not signature:
        raise HTTPException(status_code=401, detail="❌ ERROR: Missing webhook signature")

    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="❌ ERROR: Invalid webhook signature")


def _validate_logging_configuration() -> None:
    """Require JSON logs on Cloud Run so lifecycle fields are queryable."""
    if not os.getenv("K_SERVICE"):
        return
    log_format = os.getenv("ACE_LOG_FORMAT", "console").strip().lower()
    if log_format != "json":
        raise RuntimeError(
            "❌ ERROR: ACE_LOG_FORMAT must be set to json for Cloud Run webhook services."
        )


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _queue_delay_ms(*, queued_at: str | None, dequeued_at: str) -> float | None:
    if not queued_at:
        return None
    try:
        queued_dt = datetime.fromisoformat(queued_at.replace("Z", "+00:00"))
        dequeued_dt = datetime.fromisoformat(dequeued_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if queued_dt.tzinfo is None:
        queued_dt = queued_dt.replace(tzinfo=UTC)
    if dequeued_dt.tzinfo is None:
        dequeued_dt = dequeued_dt.replace(tzinfo=UTC)
    delay_ms = (dequeued_dt - queued_dt).total_seconds() * 1000
    return round(delay_ms, 3)


@app.post("/github/webhooks", status_code=202)
async def github_webhooks(request: Request) -> dict[str, Any]:
    if not _listener_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: listener endpoint disabled")

    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256")
    event = request.headers.get("X-GitHub-Event")
    delivery = request.headers.get("X-GitHub-Delivery")

    body = await request.body()
    _verify_signature(secret, body, signature)

    if not event:
        raise HTTPException(status_code=400, detail="❌ ERROR: Missing X-GitHub-Event header")

    payload = await request.json()
    settings = _get_settings()
    try:
        context = build_lifecycle_context(
            event=event,
            payload=payload,
            delivery=delivery,
            default_project=getattr(settings, "github_project_name", None),
            source="github",
            repo_gcp_mapping=_get_repo_gcp_mapping(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{exc}") from exc
    queued_at = _utc_now_iso()
    log_lifecycle_event(logger, STAGE_WEBHOOK_RECEIVED, context)

    try:
        message_id = await _get_queue().publish(
            event=event,
            payload=payload,
            delivery=delivery,
            source=context.source,
            workflow_id=context.workflow_id,
            issue_key=context.issue_key,
            project=context.project,
            target_gcp_project=context.target_gcp_project,
            action=context.action,
            queued_at=queued_at,
        )
        log_lifecycle_event(
            logger,
            STAGE_WEBHOOK_ENQUEUED,
            context,
            message_id=message_id,
            pubsub_message_id=message_id,
            queued_at=queued_at,
            pubsub_topic=getattr(settings, "webhook_pubsub_topic", None),
        )
    except Exception as exc:
        log_lifecycle_event(
            logger,
            STAGE_FINAL_RESOLUTION,
            context,
            resolution=normalize_error_resolution(exc),
            error=f"❌ ERROR: {exc}",
        )
        if _notifier is not None:
            await _notifier.safe_post(format_error_message(event, delivery, exc))
        raise HTTPException(
            status_code=500,
            detail=f"❌ ERROR: failed_to_enqueue_webhook: {exc}",
        ) from exc

    return {
        "status": "queued",
        "event": event,
        "delivery": delivery,
        "workflow_id": context.workflow_id,
        "queued_at": queued_at,
        "message_id": message_id,
        "pubsub_message_id": message_id,
    }


@app.post("/internal/pubsub/worker")
async def pubsub_worker(request: Request) -> dict[str, Any]:
    if not _worker_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: worker endpoint disabled")

    try:
        body = await request.json()
        queued = decode_pubsub_push(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{exc}") from exc

    settings = _get_settings()
    try:
        context = build_lifecycle_context(
            event=queued.event,
            payload=queued.payload,
            delivery=queued.delivery,
            source=queued.source,
            workflow_id=queued.workflow_id,
            default_project=getattr(settings, "github_project_name", None),
            project=queued.project,
            issue_key=queued.issue_key,
            target_gcp_project=queued.target_gcp_project,
            repo_gcp_mapping=_get_repo_gcp_mapping(),
            action=queued.action,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{exc}") from exc
    dequeued_at = _utc_now_iso()
    queue_delay_ms = _queue_delay_ms(queued_at=queued.queued_at, dequeued_at=dequeued_at)
    log_lifecycle_event(
        logger,
        STAGE_WORKER_DEQUEUED,
        context,
        message_id=queued.message_id,
        pubsub_message_id=queued.message_id,
        queued_at=queued.queued_at,
        dequeued_at=dequeued_at,
        queue_delay_ms=queue_delay_ms,
    )

    log_lifecycle_event(
        logger,
        STAGE_WORKER_STARTED,
        context,
        message_id=queued.message_id,
        pubsub_message_id=queued.message_id,
        queued_at=queued.queued_at,
        queue_delay_ms=queue_delay_ms,
    )

    handler = _get_handler()
    log_lifecycle_event(logger, STAGE_AGENT_STARTED, context)
    try:
        result = await handler.handle(
            queued.event,
            queued.payload,
            queued.delivery,
            workflow_id=queued.workflow_id,
        )
        log_lifecycle_event(
            logger,
            STAGE_AGENT_FINISHED,
            context,
            handler_status=result.get("status"),
            handler_action=result.get("action"),
        )
        resolution = normalize_result_resolution(result)
        log_lifecycle_event(
            logger,
            STAGE_FINAL_RESOLUTION,
            context,
            resolution=resolution,
            handler_status=result.get("status"),
            handler_action=result.get("action"),
        )
    except Exception as exc:
        resolution = normalize_error_resolution(exc)
        log_lifecycle_event(
            logger,
            STAGE_FINAL_RESOLUTION,
            context,
            resolution=resolution,
            error=f"❌ ERROR: {exc}",
        )
        if _notifier is not None:
            await _notifier.safe_post(format_error_message(queued.event, queued.delivery, exc))
        raise

    if _notifier is not None:
        message = format_webhook_message(queued.event, queued.delivery, result)
        if message is not None:
            await _notifier.safe_post(message)

    return {
        "status": "ok",
        "event": queued.event,
        "delivery": queued.delivery,
        "workflow_id": context.workflow_id,
        "queued_at": queued.queued_at,
        "queue_delay_ms": queue_delay_ms,
        "message_id": queued.message_id,
        "pubsub_message_id": queued.message_id,
        "resolution": resolution,
        "result": result,
    }


@app.post("/internal/pubsub/pr-review")
async def pr_review_worker(request: Request) -> dict[str, Any]:
    if not _worker_enabled():
        raise HTTPException(status_code=404, detail="❌ ERROR: worker endpoint disabled")

    try:
        body = await request.json()
        job = decode_pr_review_job(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{exc}") from exc

    settings = _get_settings()
    issue_key = f"{job.repo_owner}/{job.repo_name}#{job.pr_number}"
    try:
        context = build_lifecycle_context(
            event="pull_request",
            payload={
                "action": job.action,
                "number": job.pr_number,
                "repository": {
                    "name": job.repo_name,
                    "owner": {"login": job.repo_owner},
                },
            },
            delivery=job.delivery_id,
            source="github",
            workflow_id=job.workflow_id,
            default_project=getattr(settings, "github_project_name", None),
            project=job.project,
            issue_key=issue_key,
            target_gcp_project=job.target_gcp_project,
            action=job.action,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{exc}") from exc

    dequeued_at = _utc_now_iso()
    queue_delay_ms = _queue_delay_ms(queued_at=job.queued_at, dequeued_at=dequeued_at)
    log_lifecycle_event(
        logger,
        STAGE_WORKER_DEQUEUED,
        context,
        message_id=job.message_id,
        pubsub_message_id=job.message_id,
        queued_at=job.queued_at,
        dequeued_at=dequeued_at,
        queue_delay_ms=queue_delay_ms,
        pr_number=job.pr_number,
        head_sha=job.head_sha,
    )
    log_lifecycle_event(
        logger,
        STAGE_WORKER_STARTED,
        context,
        message_id=job.message_id,
        pubsub_message_id=job.message_id,
        queued_at=job.queued_at,
        queue_delay_ms=queue_delay_ms,
        pr_number=job.pr_number,
        head_sha=job.head_sha,
    )

    log_lifecycle_event(logger, STAGE_AGENT_STARTED, context, pr_number=job.pr_number)
    try:
        app_auth = _get_pr_review_app_auth()
        app_auth_token = await app_auth.get_installation_token(job.installation_id)
        async with GitHubAPIClient(app_auth_token.token) as api_client:
            issue_queue = IssueQueue(
                api_client=api_client,
                owner=job.repo_owner,
                repo=job.repo_name,
            )
            runtime = PRReviewRuntime(
                settings=settings,
                store=_get_pr_review_session_store(),
                issue_queue=issue_queue,
            )
            session = await runtime.run_review(
                repo_owner=job.repo_owner,
                repo_name=job.repo_name,
                pr_number=job.pr_number,
                head_sha=job.head_sha,
                base_branch=job.base_ref,
                action=job.action,
                workflow_id=context.workflow_id,
                session_id=job.idempotency_key,
            )

        result = session.to_dict()
        resolution = normalize_result_resolution(result)
        log_lifecycle_event(
            logger,
            STAGE_AGENT_FINISHED,
            context,
            handler_status=result.get("status"),
            handler_action=result.get("action"),
            pr_number=job.pr_number,
            head_sha=job.head_sha,
        )
        log_lifecycle_event(
            logger,
            STAGE_FINAL_RESOLUTION,
            context,
            resolution=resolution,
            handler_status=result.get("status"),
            handler_action=result.get("action"),
            pr_number=job.pr_number,
            head_sha=job.head_sha,
        )
    except Exception as exc:
        resolution = normalize_error_resolution(exc)
        log_lifecycle_event(
            logger,
            STAGE_FINAL_RESOLUTION,
            context,
            resolution=resolution,
            error=f"❌ ERROR: {exc}",
            pr_number=job.pr_number,
            head_sha=job.head_sha,
        )
        if _notifier is not None:
            await _notifier.safe_post(format_error_message("pull_request", job.delivery_id, exc))
        raise

    if _notifier is not None:
        message = format_webhook_message("pull_request", job.delivery_id, result)
        if message is not None:
            await _notifier.safe_post(message)

    return {
        "status": "ok",
        "event": "pull_request",
        "delivery": job.delivery_id,
        "workflow_id": context.workflow_id,
        "session_id": session.session_id,
        "queued_at": job.queued_at,
        "queue_delay_ms": queue_delay_ms,
        "message_id": job.message_id,
        "pubsub_message_id": job.message_id,
        "resolution": resolution,
        "result": result,
    }
