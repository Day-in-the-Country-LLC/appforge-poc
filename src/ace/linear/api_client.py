"""Linear GraphQL API client."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__).bind(component="linear_api")


class LinearAPIClient:
    """Client for Linear GraphQL API operations."""

    def __init__(
        self,
        api_key: str,
        *,
        api_url: str = "https://api.linear.app/graphql",
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url
        self.max_retries = max(0, max_retries)
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        """Execute a GraphQL query or mutation."""
        logger.debug("linear_graphql", query_length=len(query))

        attempt = 0
        while True:
            response = await self._request("POST", self.api_url, json={"query": query, "variables": variables or {}})
            if response.status_code >= 500:
                if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                    await self._sleep_retry(attempt)
                    attempt += 1
                    continue
                response.raise_for_status()

            result = response.json()
            errors = result.get("errors")
            if errors:
                raise ValueError(f"Linear GraphQL errors: {errors}")

            return result.get("data", {})

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise
                await self._sleep_retry(attempt)
                attempt += 1
                logger.warning(
                    "linear_api_transport_retry",
                    method=method,
                    url=url,
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            if self._should_retry_status(response.status_code) and attempt < self.max_retries:
                await self._sleep_retry(attempt)
                attempt += 1
                continue
            return response

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    async def _sleep_retry(self, attempt: int) -> None:
        delay = min(self.max_delay_seconds, self.base_delay_seconds * (2**attempt))
        jitter = random.uniform(0.0, self.base_delay_seconds)
        await asyncio.sleep(min(self.max_delay_seconds, delay + jitter))
