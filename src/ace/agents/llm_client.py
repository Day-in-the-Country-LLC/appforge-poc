"""LLM client helpers for planning/instructions."""

from __future__ import annotations

import structlog
import httpx

logger = structlog.get_logger(__name__)


async def call_openai(prompt: str, model: str, api_key: str, max_tokens: int = 1000) -> str:
    """Call OpenAI responses API and return text."""
    if not api_key:
        raise ValueError("OpenAI API key not configured")

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": prompt,
                "max_output_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return _extract_openai_text(data)


async def call_claude(prompt: str, model: str, api_key: str, max_tokens: int = 1000) -> str:
    """Call Anthropic messages API and return text."""
    if not api_key:
        raise ValueError("Claude API key not configured")

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]


def _extract_openai_text(data: dict) -> str:
    """Extract text from OpenAI responses API payload."""
    if "output" in data:
        output = data["output"]
        if isinstance(output, list) and output:
            item = output[0]
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, list) and content:
                    return content[0].get("text", str(content[0]))
                if "text" in item:
                    return item["text"]
            return str(item)
        return str(output)

    if "choices" in data:
        return data["choices"][0]["message"]["content"]

    logger.warning("openai_unrecognized_payload", keys=list(data.keys()))
    return str(data)
