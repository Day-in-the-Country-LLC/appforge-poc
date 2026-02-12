"""FastAPI webhook listener for GitHub events."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request

from ace.config.logging import configure_logging
from ace.config.settings import Settings, get_settings
from ace.notifications.slack_client import (
    SlackNotifier,
    format_error_message,
    format_webhook_message,
)
from ace.webhooks.handlers import WebhookHandler
from ace.webhooks.pubsub_queue import PubSubWebhookQueue, decode_pubsub_push

logger = structlog.get_logger(__name__)

app = FastAPI()
_handler: WebhookHandler | None = None
_notifier: SlackNotifier | None = None
_queue: PubSubWebhookQueue | None = None
_settings: Settings | None = None


@app.on_event("startup")
async def _startup() -> None:
    settings = _get_settings()
    configure_logging(debug=settings.debug)
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
        raise HTTPException(
            status_code=500, detail="❌ ERROR: GITHUB_WEBHOOK_SECRET not set"
        )
    if not signature:
        raise HTTPException(
            status_code=401, detail="❌ ERROR: Missing webhook signature"
        )

    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(
            status_code=401, detail="❌ ERROR: Invalid webhook signature"
        )


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
        raise HTTPException(
            status_code=400, detail="❌ ERROR: Missing X-GitHub-Event header"
        )

    payload = await request.json()

    try:
        message_id = await _get_queue().publish(event=event, payload=payload, delivery=delivery)
    except Exception as exc:
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
        "message_id": message_id,
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

    handler = _get_handler()
    try:
        result = await handler.handle(queued.event, queued.payload, queued.delivery)
    except Exception as exc:
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
        "message_id": queued.message_id,
        "result": result,
    }
