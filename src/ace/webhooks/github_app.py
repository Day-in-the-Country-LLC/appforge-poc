"""GitHub App authentication helpers for webhook processing."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
import jwt
import structlog

logger = structlog.get_logger(__name__)

GITHUB_API_URL = "https://api.github.com"


@dataclass
class InstallationToken:
    token: str
    expires_at: str | None = None


class GitHubAppAuth:
    """Generate installation tokens for a GitHub App."""

    def __init__(self, app_id: str, private_key: str) -> None:
        if not app_id:
            raise ValueError("❌ ERROR: GITHUB_APP_ID is missing")
        if not private_key:
            raise ValueError("❌ ERROR: GITHUB_APP_PRIVATE_KEY is missing")
        self.app_id = app_id
        self.private_key = private_key
        self._token_cache: dict[int, InstallationToken] = {}

    @classmethod
    def from_env(cls) -> GitHubAppAuth:
        app_id = os.getenv("GITHUB_APP_ID", "").strip()
        private_key = os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
        if "\\n" in private_key:
            private_key = private_key.replace("\\n", "\n")
        return cls(app_id=app_id, private_key=private_key)

    def _create_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 30,
            "exp": now + 540,
            "iss": self.app_id,
        }
        token = jwt.encode(payload, self.private_key, algorithm="RS256")
        return token if isinstance(token, str) else token.decode("utf-8")

    async def get_installation_token(self, installation_id: int) -> InstallationToken:
        cached = self._token_cache.get(installation_id)
        if cached:
            return cached

        jwt_token = self._create_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        }
        url = f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)

        if response.status_code >= 400:
            logger.error(
                "github_app_token_failed",
                status=response.status_code,
                body=response.text,
            )
            raise ValueError(
                f"❌ ERROR: GitHub App token request failed ({response.status_code})"
            )

        payload = response.json()
        token = payload.get("token")
        if not token:
            raise ValueError("❌ ERROR: GitHub App token missing in response")

        result = InstallationToken(token=token, expires_at=payload.get("expires_at"))
        self._token_cache[installation_id] = result
        return result
