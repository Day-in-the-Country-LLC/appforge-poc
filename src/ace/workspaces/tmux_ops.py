"""Tmux session utilities for agent execution."""

from pathlib import Path
import subprocess
import time
import re

import structlog

logger = structlog.get_logger(__name__)

SESSION_PREFIX = "ace-"


def session_name_for_issue(repo_name: str, issue_number: int | str) -> str:
    raw = f"{SESSION_PREFIX}{repo_name}-{issue_number}"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")
    return slug[:60] if len(slug) > 60 else slug


def parse_issue_from_session(session_name: str) -> tuple[str, int] | None:
    if not session_name.startswith(SESSION_PREFIX):
        return None

    parts = session_name.split("-")
    if len(parts) < 3:
        return None

    issue_part = parts[-1]
    if not issue_part.isdigit():
        return None

    repo_slug = "-".join(parts[1:-1])
    return repo_slug, int(issue_part)


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

    def list_sessions(self) -> list[tuple[str, int]]:
        """Return a list of (session_name, session_activity_epoch)."""
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        sessions: list[tuple[str, int]] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            name, activity = parts
            try:
                sessions.append((name, int(activity)))
            except ValueError:
                continue
        return sessions

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

    def kill_session(self, session_name: str) -> None:
        """Kill a tmux session if it exists."""
        if not self.session_exists(session_name):
            return

        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning(
                "tmux_kill_failed",
                session=session_name,
                error=result.stderr.decode("utf-8", errors="replace").strip(),
            )

    def nudge_session(self, session_name: str, message: str) -> None:
        """Send a reliable nudge to a tmux session."""
        if not message:
            return
        if not self.session_exists(session_name):
            raise RuntimeError(f"tmux session '{session_name}' not found")

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "-l", message],
            check=True,
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.5)
        last_error = ""
        for attempt in range(3):
            if attempt > 0:
                time.sleep(0.2)
            result = subprocess.run(
                ["tmux", "send-keys", "-t", session_name, "Enter"],
                check=False,
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return
            last_error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"failed to send Enter to tmux session '{session_name}': {last_error}"
        )

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
