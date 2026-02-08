"""FastAPI webhook listener for GitHub events."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
import structlog

from ace.config.logging import configure_logging
from ace.config.settings import get_settings
from ace.webhooks.handlers import WebhookHandler

logger = structlog.get_logger(__name__)

app = FastAPI()
_handler: WebhookHandler | None = None


@app.on_event("startup")
async def _startup() -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug)
    global _handler
    _handler = WebhookHandler()
    logger.info("webhook_listener_started")


def _verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not secret:
        raise HTTPException(status_code=500, detail="❌ ERROR: GITHUB_WEBHOOK_SECRET not set")
    if not signature:
        raise HTTPException(status_code=401, detail="❌ ERROR: Missing webhook signature")

    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="❌ ERROR: Invalid webhook signature")


@app.post("/github/webhooks")
async def github_webhooks(request: Request) -> dict[str, Any]:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256")
    event = request.headers.get("X-GitHub-Event")
    delivery = request.headers.get("X-GitHub-Delivery")

    body = await request.body()
    _verify_signature(secret, body, signature)

    if not event:
        raise HTTPException(status_code=400, detail="❌ ERROR: Missing X-GitHub-Event header")

    payload = await request.json()
    if _handler is None:
        raise HTTPException(status_code=500, detail="❌ ERROR: Webhook handler not initialized")

    result = await _handler.handle(event, payload, delivery)
    return {"status": "ok", "result": result}
