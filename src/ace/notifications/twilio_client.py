"""Twilio SMS notification client."""

import structlog
from twilio.rest import Client

from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)


class TwilioNotifier:
    """Sends SMS notifications via Twilio."""

    def __init__(self):
        """Initialize Twilio client with settings."""
        self.settings = get_settings()
        self.enabled = self.settings.twilio_enabled

        if self.enabled:
            self.client = Client(
                self.settings.twilio_account_sid,
                self.settings.twilio_auth_token,
            )
        else:
            self.client = None

    async def send_pr_notification(
        self,
        pr_number: int,
        pr_url: str,
        issue_number: int,
        issue_title: str,
        repo_name: str,
        summary: str,
    ) -> bool:
        """Send SMS notification about a new PR.

        Args:
            pr_number: Pull request number
            pr_url: Pull request URL
            issue_number: Related issue number
            issue_title: Issue title
            repo_name: Repository name
            summary: Brief summary of work completed

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.info("twilio_disabled", skipping_notification=True)
            return False

        try:
            message_body = self._format_pr_message(
                pr_number=pr_number,
                pr_url=pr_url,
                issue_number=issue_number,
                issue_title=issue_title,
                repo_name=repo_name,
                summary=summary,
            )

            logger.info(
                "sending_sms",
                to=self.settings.twilio_to_number,
                pr=pr_number,
                message_length=len(message_body),
            )

            message = self.client.messages.create(
                body=message_body,
                messaging_service_sid=self.settings.twilio_messaging_service_sid,
                to=self.settings.twilio_to_number,
            )

            logger.info("sms_sent", message_id=message.sid, pr=pr_number)
            return True

        except Exception as e:
            logger.error("sms_send_failed", pr=pr_number, error=str(e))
            return False

    async def send_blocked_notification(
        self,
        issue_number: int,
        issue_title: str,
        question: str,
    ) -> bool:
        """Send SMS notification when agent is blocked.

        Args:
            issue_number: Issue number
            issue_title: Issue title
            question: Question from the agent

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.info("twilio_disabled", skipping_notification=True)
            return False

        try:
            message_body = self._format_blocked_message(
                issue_number=issue_number,
                issue_title=issue_title,
                question=question,
            )

            logger.info(
                "sending_blocked_sms",
                to=self.settings.twilio_to_number,
                issue=issue_number,
            )

            message = self.client.messages.create(
                body=message_body,
                messaging_service_sid=self.settings.twilio_messaging_service_sid,
                to=self.settings.twilio_to_number,
            )

            logger.info("blocked_sms_sent", message_id=message.sid, issue=issue_number)
            return True

        except Exception as e:
            logger.error("blocked_sms_send_failed", issue=issue_number, error=str(e))
            return False

    def _format_pr_message(
        self,
        pr_number: int,
        pr_url: str,
        issue_number: int,
        issue_title: str,
        repo_name: str,
        summary: str,
    ) -> str:
        """Format PR notification message.

        Args:
            pr_number: Pull request number
            pr_url: Pull request URL
            issue_number: Related issue number
            issue_title: Issue title
            repo_name: Repository name
            summary: Brief summary

        Returns:
            Formatted message
        """
        return (
            f"🚀 PR Ready for Review\n\n"
            f"Issue #{issue_number}: {issue_title}\n"
            f"Repo: {repo_name}\n"
            f"PR: #{pr_number}\n\n"
            f"Summary:\n{summary}\n\n"
            f"Review: {pr_url}"
        )

    def _format_blocked_message(
        self,
        issue_number: int,
        issue_title: str,
        question: str,
    ) -> str:
        """Format blocked notification message.

        Args:
            issue_number: Issue number
            issue_title: Issue title
            question: Question from agent

        Returns:
            Formatted message
        """
        return (
            f"⏸️ Agent Blocked\n\n"
            f"Issue #{issue_number}: {issue_title}\n\n"
            f"Question:\n{question}\n\n"
            f"Reply in GitHub to resume."
        )
