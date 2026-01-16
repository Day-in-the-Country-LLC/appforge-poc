"""GitHub MCP client wrapper for tool invocation."""

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class GitHubMCPClient:
    """Wrapper around GitHub MCP server for tool invocation."""

    def __init__(self, mcp_server_url: str, auth_token: str):
        """Initialize the MCP client.

        Args:
            mcp_server_url: URL of the GitHub MCP server
            auth_token: Authentication token for the MCP server
        """
        self.mcp_server_url = mcp_server_url
        self.auth_token = auth_token
        self.client = httpx.AsyncClient(
            base_url=mcp_server_url,
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=30.0,
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the GitHub MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Result from the tool invocation
        """
        logger.info("calling_mcp_tool", tool=tool_name, arguments=arguments)
        try:
            response = await self.client.post(
                "/tools/call",
                json={"tool": tool_name, "arguments": arguments},
            )
            response.raise_for_status()
            result = response.json()
            logger.info("mcp_tool_success", tool=tool_name, result=result)
            return result
        except httpx.HTTPError as e:
            logger.error("mcp_tool_failed", tool=tool_name, error=str(e))
            raise

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
