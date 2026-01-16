"""Tmux session utilities for agent execution."""

from pathlib import Path
import subprocess
import time

import structlog

logger = structlog.get_logger(__name__)


class TmuxOps:
    """Minimal tmux session management."""

    def session_exists(self, session_name: str) -> bool:
        """Check if a tmux session exists."""
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    def start_session(
        self,
        session_name: str,
        workdir: Path,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> bool:
        """Start a tmux session in detached mode.

        Returns True if the session was created, False if it already existed.
        """
        if self.session_exists(session_name):
            logger.info("tmux_session_exists", session=session_name)
            return False

        cmd = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            str(workdir),
        ]
        if command:
            cmd.append("--")
            cmd.extend(command)

        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        if env:
            for key, value in env.items():
                subprocess.run(
                    ["tmux", "set-environment", "-t", session_name, key, value],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            logger.info("tmux_env_set", session=session_name, keys=list(env.keys()))
        logger.info("tmux_session_started", session=session_name, workdir=str(workdir))
        return True

    def send_prompt(self, session_name: str, prompt: str, delay_seconds: float = 1.0) -> None:
        """Send a prompt to an existing session."""
        if not prompt:
            return

        if delay_seconds > 0:
            time.sleep(delay_seconds)

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "-l", prompt],
            check=True,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            check=True,
            capture_output=True,
            timeout=5,
        )
