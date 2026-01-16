"""Scheduler for triggering agent runs on a schedule."""

import asyncio
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from ace.config.settings import get_settings
from ace.runners.agent_pool import AgentTarget, get_pool

logger = structlog.get_logger(__name__)

DEFAULT_RUN_TIME = time(8, 0)  # 8:00 AM
DEFAULT_TIMEZONE = "America/New_York"


class DailyScheduler:
    """Schedules daily agent runs at a specific time."""

    def __init__(
        self,
        run_time: time = DEFAULT_RUN_TIME,
        timezone: str = DEFAULT_TIMEZONE,
    ):
        """Initialize the scheduler.

        Args:
            run_time: Time of day to run (default: 8:00 AM)
            timezone: Timezone for the schedule (default: America/New_York)
        """
        self.run_time = run_time
        self.timezone = ZoneInfo(timezone)
        self._running = False
        self.settings = get_settings()

    def _get_next_run_time(self) -> datetime:
        """Calculate the next scheduled run time."""
        now = datetime.now(self.timezone)
        today_run = now.replace(
            hour=self.run_time.hour,
            minute=self.run_time.minute,
            second=0,
            microsecond=0,
        )

        if now >= today_run:
            # Already past today's run time, schedule for tomorrow
            return today_run + timedelta(days=1)
        return today_run

    def _seconds_until_next_run(self) -> float:
        """Calculate seconds until the next scheduled run."""
        next_run = self._get_next_run_time()
        now = datetime.now(self.timezone)
        return (next_run - now).total_seconds()

    async def run_daily(self) -> None:
        """Run the scheduler, triggering agent runs at the scheduled time.

        This runs indefinitely until stopped.
        """
        self._running = True
        logger.info(
            "daily_scheduler_starting",
            run_time=self.run_time.isoformat(),
            timezone=str(self.timezone),
        )

        while self._running:
            next_run = self._get_next_run_time()
            seconds_until = self._seconds_until_next_run()

            logger.info(
                "next_scheduled_run",
                next_run=next_run.isoformat(),
                seconds_until=seconds_until,
            )

            # Wait until the scheduled time
            await asyncio.sleep(seconds_until)

            if not self._running:
                break

            # Trigger the agent run
            logger.info("scheduled_run_triggered", time=datetime.now(self.timezone).isoformat())

            try:
                # Only process remote issues from cloud scheduler
                pool = get_pool(AgentTarget.REMOTE)
                result = await pool.run_until_empty()
                logger.info("scheduled_run_complete", result=result)
            except Exception as e:
                logger.error("scheduled_run_failed", error=str(e))

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        logger.info("daily_scheduler_stopped")

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status."""
        next_run = self._get_next_run_time()
        return {
            "running": self._running,
            "run_time": self.run_time.isoformat(),
            "timezone": str(self.timezone),
            "next_run": next_run.isoformat(),
            "seconds_until_next": self._seconds_until_next_run(),
        }


# Global scheduler instance
_scheduler: DailyScheduler | None = None


def get_scheduler(
    run_time: time = DEFAULT_RUN_TIME,
    timezone: str = DEFAULT_TIMEZONE,
) -> DailyScheduler:
    """Get or create the global scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = DailyScheduler(run_time=run_time, timezone=timezone)
    return _scheduler
