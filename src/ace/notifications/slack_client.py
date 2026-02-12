"""Slack notification client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from ace.config.settings import Settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SlackMessage:
    text: str


class SlackNotifier:
    """Sends notifications to Slack via bot token."""

    def __init__(self, token: str, channel_id: str) -> None:
        self._token = token
        self._channel_id = channel_id

    @classmethod
    def from_settings(cls, settings: Settings) -> SlackNotifier | None:
        token = settings.slack_bot_token
        channel_id = settings.slack_channel_id
        if not token and not channel_id:
            return None
        if not token or not channel_id:
            raise ValueError(
                "❌ ERROR: Slack notifications require SLACK_BOT_TOKEN (or SLACKBOT_TOKEN) "
                "and SLACK_CHANNEL_ID"
            )
        return cls(token, channel_id)

    async def post(self, message: SlackMessage) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {"channel": self._channel_id, "text": message.text}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers=headers,
            )

        data: dict[str, Any]
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400:
            raise ValueError(f"❌ ERROR: Slack API error {response.status_code}: {response.text}")
        if not data.get("ok"):
            raise ValueError(f"❌ ERROR: Slack API error: {data.get('error', 'unknown')}")

    async def safe_post(self, message: SlackMessage) -> None:
        try:
            await self.post(message)
        except Exception as exc:
            logger.error("slack_notification_failed", error=f"❌ ERROR: {exc}")


def format_webhook_message(
    event: str | None, delivery: str | None, result: dict[str, Any]
) -> SlackMessage | None:
    status = result.get("status")
    if status in ("ignored", "no_matching_transition"):
        return None

    action = result.get("action") or event or "unknown"
    issue = result.get("issue")
    repo = result.get("repo")
    blocked = result.get("blockers")
    triggered_count = result.get("triggered_count")

    lines = ["Webhook processed", f"event={event or 'unknown'}", f"action={action}"]

    if repo and issue:
        lines.append(f"issue={repo}#{issue}")
    if status:
        lines.append(f"status={status}")
    if blocked:
        lines.append(f"blocked={blocked}")
    if triggered_count is not None:
        lines.append(f"triggered_count={triggered_count}")
    if delivery:
        lines.append(f"delivery={delivery}")

    return SlackMessage(text=" | ".join(lines))


def format_error_message(event: str | None, delivery: str | None, exc: Exception) -> SlackMessage:
    return SlackMessage(
        text=(
            "Webhook error | "
            f"event={event or 'unknown'} | "
            f"delivery={delivery or 'unknown'} | "
            f"error={exc}"
        )
    )


def format_completion_message(
    *,
    status: str,
    issue_number: int | None,
    repo: str | None,
    summary: str | None,
    blocked_questions: list[str] | None,
    error: str | None,
    output: str | None,
    pr_url: str | None,
) -> SlackMessage | None:
    status_lower = (status or "").lower()
    header = None
    if status_lower == "completed":
        header = "✅ ACE task completed"
    elif status_lower == "blocked":
        header = "⏸️ ACE task blocked"
    elif status_lower == "failed":
        header = "❌ ACE task failed"
    else:
        return None

    lines = [header]
    if repo and issue_number:
        lines.append(f"Issue: {repo}#{issue_number}")
    elif issue_number:
        lines.append(f"Issue: #{issue_number}")
    if pr_url:
        lines.append(f"PR: {pr_url}")

    if status_lower == "failed":
        if error:
            lines.append(f"Error: {_truncate(error)}")
        if output and output != error:
            lines.append(f"Logs: {_truncate(output)}")
    else:
        if summary:
            lines.append("Summary:")
            lines.append(_truncate(summary))
        if status_lower == "blocked" and blocked_questions:
            lines.append("Blocked questions:")
            for question in blocked_questions:
                lines.append(f"- {question}")

    return SlackMessage(text="\n".join(lines))


def _truncate(value: str, limit: int = 1400) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
