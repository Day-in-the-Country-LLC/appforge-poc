"""GitHub API client for REST and GraphQL operations."""

from typing import Any

import httpx
import structlog

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
        response = await self.client.get(url, params=params)
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
        response = await self.client.post(url, json=json)
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
        response = await self.client.patch(url, json=json)
        response.raise_for_status()
        return response.json()

    async def rest_delete(self, endpoint: str) -> None:
        """Make a DELETE request to GitHub REST API.

        Args:
            endpoint: API endpoint
        """
        url = f"{GITHUB_API_URL}{endpoint}"
        logger.debug("github_rest_delete", endpoint=endpoint)
        response = await self.client.delete(url)
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
        response = await self.client.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
        )
        response.raise_for_status()
        result = response.json()
        if "errors" in result:
            logger.error("github_graphql_errors", errors=result["errors"])
            raise ValueError(f"GraphQL errors: {result['errors']}")
        return result.get("data")

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
