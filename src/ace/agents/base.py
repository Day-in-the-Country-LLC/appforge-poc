"""Base agent interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    """Agent execution status."""

    PLANNING = "planning"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class AgentResult:
    """Result of agent execution."""

    status: AgentStatus
    output: str
    files_changed: list[str]
    commands_run: list[str]
    blocked_questions: list[str] | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None


class BaseAgent(ABC):
    """Base class for all agent implementations."""

    @abstractmethod
    async def plan(self, task: str, context: dict[str, Any]) -> str:
        """Generate a plan for the task.

        Args:
            task: Task description
            context: Context information (issue body, repo info, etc.)

        Returns:
            Plan as a string
        """
        pass

    @abstractmethod
    async def run(
        self,
        task: str,
        context: dict[str, Any],
        workspace_path: str,
    ) -> AgentResult:
        """Execute the task in the given workspace.

        Args:
            task: Task description
            context: Context information
            workspace_path: Path to the workspace directory

        Returns:
            AgentResult with execution details
        """
        pass

    @abstractmethod
    async def respond_to_answer(
        self,
        answer: str,
        previous_result: AgentResult,
        workspace_path: str,
    ) -> AgentResult:
        """Resume execution after receiving an answer to a blocked question.

        Args:
            answer: Answer to the blocked question
            previous_result: Previous execution result
            workspace_path: Path to the workspace directory

        Returns:
            AgentResult with updated execution details
        """
        pass
