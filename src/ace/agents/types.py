"""Shared agent result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    """Status for a single agent execution."""

    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class AgentResult:
    """Result of a single agent execution."""

    status: AgentStatus
    output: str
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] | None = None
