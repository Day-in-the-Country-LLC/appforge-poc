"""Codex (OpenAI) agent implementation."""

from typing import Any

import httpx
import structlog

from ace.config.settings import get_settings

from .base import AgentResult, AgentStatus, BaseAgent

logger = structlog.get_logger(__name__)


class CodexAgent(BaseAgent):
    """Agent that uses OpenAI Codex/GPT for code generation."""

    def __init__(self):
        """Initialize the Codex agent."""
        self.settings = get_settings()
        self.api_key = self.settings.openai_api_key
        self.model = self.settings.codex_model

    async def plan(self, task: str, context: dict[str, Any]) -> str:
        """Generate a plan for the task using Codex."""
        logger.info("codex_planning", task=task[:100])

        prompt = f"""You are a coding agent. Analyze this task and create a step-by-step plan.

Task: {task}

Context:
- Repository: {context.get("repo_name", "unknown")}
- Issue: #{context.get("issue_number", "unknown")}

Provide a concise plan with numbered steps."""

        response = await self._call_openai(prompt, max_tokens=500)
        return response

    async def run(
        self,
        task: str,
        context: dict[str, Any],
        workspace_path: str,
    ) -> AgentResult:
        """Execute the coding task using Codex."""
        logger.info(
            "codex_running",
            task=task[:100],
            workspace=workspace_path,
            model=self.model,
        )

        from ace.agents.policy import get_policy_prompt

        policy = get_policy_prompt()

        prompt = f"""{policy}

You are a coding agent working on a GitHub issue.

## Task
{task}

## Context
- Repository: {context.get("repo_name", "unknown")}
- Issue: #{context.get("issue_number", "unknown")}
- Workspace: {workspace_path}

## Instructions
1. Analyze the task requirements
2. Generate the code changes needed
3. If you need clarification, respond with BLOCKED: followed by your questions
4. If you can complete the task, respond with SUCCESS: followed by a summary

Respond with either:
- SUCCESS: <summary of changes made>
- BLOCKED: <questions that need answers>
- FAILED: <reason for failure>
"""

        try:
            response = await self._call_openai(prompt, max_tokens=2000)
            return self._parse_response(response)
        except Exception as e:
            logger.error("codex_run_failed", error=str(e))
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error=str(e),
            )

    async def respond_to_answer(
        self,
        answer: str,
        previous_result: AgentResult,
        workspace_path: str,
    ) -> AgentResult:
        """Resume after receiving an answer."""
        logger.info("codex_resuming", answer=answer[:100])

        prompt = f"""You previously asked questions about a task and received this answer:

Answer: {answer}

Previous context: {previous_result.output}

Continue with the task based on this answer. Respond with SUCCESS, BLOCKED, or FAILED."""

        try:
            response = await self._call_openai(prompt, max_tokens=2000)
            return self._parse_response(response)
        except Exception as e:
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error=str(e),
            )

    async def _call_openai(self, prompt: str, max_tokens: int = 1000) -> str:
        """Call OpenAI API."""
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _parse_response(self, response: str) -> AgentResult:
        """Parse agent response into AgentResult."""
        response = response.strip()

        if response.startswith("SUCCESS:"):
            return AgentResult(
                status=AgentStatus.SUCCESS,
                output=response[8:].strip(),
                files_changed=[],
                commands_run=[],
            )
        elif response.startswith("BLOCKED:"):
            questions = response[8:].strip().split("\n")
            return AgentResult(
                status=AgentStatus.BLOCKED,
                output=response,
                files_changed=[],
                commands_run=[],
                blocked_questions=[q.strip() for q in questions if q.strip()],
            )
        elif response.startswith("FAILED:"):
            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                files_changed=[],
                commands_run=[],
                error=response[7:].strip(),
            )
        else:
            # Assume success if no prefix
            return AgentResult(
                status=AgentStatus.SUCCESS,
                output=response,
                files_changed=[],
                commands_run=[],
            )
