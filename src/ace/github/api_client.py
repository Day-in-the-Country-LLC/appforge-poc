"""GitHub API client for REST and GraphQL operations."""

import asyncio
import random
import time
from typing import Any

import httpx
import structlog

from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubAPIClient:
    """Client for GitHub REST and GraphQL API operations."""

    def __init__(self, token: str):
        """Initialize the GitHub API client.

        Args:
            token: GitHub Personal Access Token
        """
        self.token = token
        self._client: httpx.AsyncClient | None = None
        self._settings = get_settings()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self._client

    async def rest_get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to GitHub REST API.

        Args:
            endpoint: API endpoint (e.g., "/repos/owner/repo/issues")
            params: Query parameters

        Returns:
            JSON response
        """
        url = f"{GITHUB_API_URL}{endpoint}"
        logger.debug("github_rest_get", endpoint=endpoint, params=params)
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        return response.json()

    async def rest_post(self, endpoint: str, json: dict[str, Any]) -> Any:
        """Make a POST request to GitHub REST API.

        Args:
            endpoint: API endpoint
            json: Request body

        Returns:
            JSON response
        """
        url = f"{GITHUB_API_URL}{endpoint}"
        logger.debug("github_rest_post", endpoint=endpoint)
        response = await self._request("POST", url, json=json)
        response.raise_for_status()
        return response.json()

    async def rest_patch(self, endpoint: str, json: dict[str, Any]) -> Any:
        """Make a PATCH request to GitHub REST API.

        Args:
            endpoint: API endpoint
            json: Request body

        Returns:
            JSON response
        """
        url = f"{GITHUB_API_URL}{endpoint}"
        logger.debug("github_rest_patch", endpoint=endpoint)
        response = await self._request("PATCH", url, json=json)
        response.raise_for_status()
        return response.json()

    async def rest_delete(self, endpoint: str) -> None:
        """Make a DELETE request to GitHub REST API.

        Args:
            endpoint: API endpoint
        """
        url = f"{GITHUB_API_URL}{endpoint}"
        logger.debug("github_rest_delete", endpoint=endpoint)
        response = await self._request("DELETE", url)
        response.raise_for_status()

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string
            variables: Query variables

        Returns:
            GraphQL response data
        """
        logger.debug("github_graphql", query_length=len(query))
        max_retries = self._settings.github_api_max_retries
        attempt = 0
        while True:
            response = await self._request(
                "POST",
                GITHUB_GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
            )
            response.raise_for_status()
            result = response.json()
            errors = result.get("errors")
            if not errors:
                return result.get("data")

            if self._is_graphql_rate_limited(errors):
                if attempt >= max_retries:
                    logger.error("github_graphql_rate_limit_exhausted", errors=errors)
                    raise ValueError(f"GraphQL rate limit exceeded: {errors}")
                delay = self._retry_delay(response, attempt)
                logger.warning(
                    "github_graphql_rate_limit_retry",
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue

            logger.error("github_graphql_errors", errors=errors)
            raise ValueError(f"GraphQL errors: {errors}")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        max_retries = self._settings.github_api_max_retries
        attempt = 0
        while True:
            try:
                response = await self.client.request(method, url, **kwargs)
            except httpx.TransportError as e:
                if attempt >= max_retries:
                    raise
                delay = self._retry_delay(None, attempt)
                logger.warning(
                    "github_api_retry_transport",
                    method=method,
                    url=url,
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                    error=str(e),
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue

            if response.status_code >= 400 and self._should_retry(response):
                if attempt >= max_retries:
                    return response
                delay = self._retry_delay(response, attempt)
                logger.warning(
                    "github_api_retry",
                    method=method,
                    url=url,
                    status=response.status_code,
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue

            return response

    def _should_retry(self, response: httpx.Response) -> bool:
        status = response.status_code
        if status in {429, 500, 502, 503, 504}:
            return True

        if status == 403:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                return True
            if "Retry-After" in response.headers:
                return True

        return False

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        header_delay = self._rate_limit_delay(response)
        if header_delay is not None:
            return header_delay

        base = self._settings.github_api_retry_base_seconds
        max_delay = self._settings.github_api_retry_max_seconds
        backoff = base * (2**attempt)
        jitter = random.uniform(0, base)
        return min(max_delay, backoff + jitter)

    def _rate_limit_delay(self, response: httpx.Response | None) -> float | None:
        if not response:
            return None

        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                return None

        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining == "0" and reset:
            try:
                reset_time = int(reset)
            except ValueError:
                return None
            return max(0.0, reset_time - time.time()) + 1.0
        return None

    def _is_graphql_rate_limited(self, errors: list[dict[str, Any]]) -> bool:
        for error in errors:
            message = str(error.get("message", "")).lower()
            error_type = str(error.get("type", "")).lower()
            if "rate limit" in message or "rate limit" in error_type:
                return True
        return False
