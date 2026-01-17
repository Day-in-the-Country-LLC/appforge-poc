"""LLM client helpers for planning/instructions."""

from __future__ import annotations

import structlog
import httpx

logger = structlog.get_logger(__name__)


class _LangSmithTracer:
    def __init__(self) -> None:
        self._enabled = False
        self._client = None
        self._project = "ace"
        self._log_prompts = True
        self._log_responses = True
        self._endpoint = None
        self._api_key = ""

        try:
            from ace.config.settings import get_settings
            from ace.config.secrets import resolve_langsmith_api_key

            settings = get_settings()
            api_key = resolve_langsmith_api_key(settings)
            self._enabled = bool(settings.langsmith_enabled and api_key)
            self._api_key = api_key
            self._project = settings.langsmith_project
            self._endpoint = settings.langsmith_endpoint
            self._log_prompts = settings.langsmith_log_prompts
            self._log_responses = settings.langsmith_log_responses
        except Exception as e:
            logger.warning("langsmith_settings_load_failed", error=str(e))
            self._enabled = False

        if self._enabled:
            try:
                from langsmith import Client

                self._client = Client(api_url=self._endpoint, api_key=self._api_key)
            except Exception as e:
                logger.warning("langsmith_client_init_failed", error=str(e))
                self._client = None
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def start_run(
        self,
        name: str,
        inputs: dict,
        metadata: dict | None,
        tags: list[str],
    ) -> str | None:
        if not self.enabled:
            return None

        payload = inputs.copy()
        if not self._log_prompts and "prompt" in payload:
            payload = {k: v for k, v in payload.items() if k != "prompt"}
            payload["prompt_length"] = len(inputs.get("prompt", ""))

        extra = {"metadata": metadata or {}}
        try:
            run = self._client.create_run(
                name=name,
                inputs=payload,
                run_type="llm",
                project_name=self._project,
                tags=tags,
                extra=extra,
            )
            return run.get("id") if isinstance(run, dict) else getattr(run, "id", None)
        except Exception as e:
            logger.warning("langsmith_create_run_failed", error=str(e))
            return None

    def end_run(self, run_id: str | None, outputs: dict | None, error: str | None) -> None:
        if not self.enabled or not run_id:
            return

        payload = outputs or {}
        if outputs and not self._log_responses and "response" in outputs:
            payload = {k: v for k, v in outputs.items() if k != "response"}
            payload["response_length"] = len(outputs.get("response", ""))

        try:
            self._client.update_run(
                run_id,
                outputs=payload,
                error=error,
            )
        except Exception as e:
            logger.warning("langsmith_update_run_failed", error=str(e))


_TRACER: _LangSmithTracer | None = None


def _get_tracer() -> _LangSmithTracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = _LangSmithTracer()
    return _TRACER


async def call_openai(
    prompt: str,
    model: str,
    api_key: str,
    max_tokens: int = 1000,
    *,
    trace_name: str = "openai_call",
    metadata: dict | None = None,
) -> str:
    """Call OpenAI responses API and return text."""
    if not api_key:
        raise ValueError("OpenAI API key not configured")

    tracer = _get_tracer()
    run_id = tracer.start_run(
        trace_name,
        inputs={"prompt": prompt, "model": model, "max_tokens": max_tokens},
        metadata=metadata,
        tags=["openai"],
    )
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
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
            output = _extract_openai_text(data)
            tracer.end_run(run_id, {"response": output}, None)
            return output
        except Exception as e:
            tracer.end_run(run_id, None, str(e))
            raise


async def call_claude(
    prompt: str,
    model: str,
    api_key: str,
    max_tokens: int = 1000,
    *,
    trace_name: str = "claude_call",
    metadata: dict | None = None,
) -> str:
    """Call Anthropic messages API and return text."""
    if not api_key:
        raise ValueError("Claude API key not configured")

    tracer = _get_tracer()
    run_id = tracer.start_run(
        trace_name,
        inputs={"prompt": prompt, "model": model, "max_tokens": max_tokens},
        metadata=metadata,
        tags=["claude"],
    )
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
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
            output = data["content"][0]["text"]
            tracer.end_run(run_id, {"response": output}, None)
            return output
        except Exception as e:
            tracer.end_run(run_id, None, str(e))
            raise


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
