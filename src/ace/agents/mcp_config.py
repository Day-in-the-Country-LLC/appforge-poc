"""MCP configuration helpers for CLI agents."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from ace.config.settings import Settings

logger = structlog.get_logger(__name__)


def ensure_mcp_config(
    workdir: Path,
    backend: str,
    token: str,
    settings: Settings,
) -> None:
    """Ensure the MCP config exists for the given backend."""
    if not token:
        raise ValueError("GitHub token missing; cannot configure MCP servers.")

    config_path = workdir / settings.mcp_config_filename
    backend = backend.lower()

    include_appforge_server = settings.appforge_mcp_enabled and settings.appforge_mcp_url

    if backend == "claude":
        payload = _claude_http_config(settings, token)
        _write_mcp_config(config_path, payload, settings.mcp_server_name)
        logger.info("mcp_config_written_github", path=str(config_path))
        if include_appforge_server:
            appforge_payload = _appforge_http_config(settings)
            _write_mcp_config(
                config_path, appforge_payload, settings.appforge_mcp_server_name
            )
            logger.info("mcp_config_written_appforge", path=str(config_path))
        _ensure_git_exclude(workdir, settings.mcp_config_filename)
        return

    if backend == "codex":
        codex_url = _normalize_mcp_url(settings.codex_mcp_url)
        codex_config = Path(settings.codex_config_path).expanduser()
        _write_codex_server(
            Path(settings.codex_config_path).expanduser(),
            settings.mcp_server_name,
            codex_url,
            settings.github_mcp_token_env,
        )
        logger.info("codex_mcp_config_written_github", path=str(codex_config))
        if include_appforge_server:
            _write_codex_server(
                codex_config,
                settings.appforge_mcp_server_name,
                _normalize_mcp_url(settings.appforge_mcp_url),
            )
            logger.info("codex_mcp_config_written_appforge", path=str(codex_config))
        return

    logger.info("mcp_config_skipped", backend=backend)


def _claude_http_config(settings: Settings, token: str) -> dict:
    url = _normalize_mcp_url(settings.claude_mcp_url)
    return {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _write_mcp_config(config_path: Path, server_payload: dict, server_name: str) -> None:
    data = {"mcpServers": {server_name: server_payload}}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                mcp_servers = existing.get("mcpServers")
                if isinstance(mcp_servers, dict):
                    mcp_servers[server_name] = server_payload
                    existing["mcpServers"] = mcp_servers
                    data = existing
        except json.JSONDecodeError:
            logger.warning("mcp_config_parse_failed", path=str(config_path))

    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("mcp_config_written", path=str(config_path))


def _ensure_git_exclude(workdir: Path, filename: str) -> None:
    exclude_path = workdir / ".git" / "info" / "exclude"
    if not exclude_path.exists():
        return

    content = exclude_path.read_text(encoding="utf-8")
    entry = f"\n{filename}\n"
    if filename in content:
        return
    exclude_path.write_text(content + entry, encoding="utf-8")


def _appforge_http_config(settings: Settings) -> dict:
    return {
        "type": "http",
        "url": _normalize_mcp_url(settings.appforge_mcp_url),
    }


def _write_codex_server(
    config_path: Path, server_name: str, url: str, token_env_var: str | None = None
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    block = f"[mcp_servers.{server_name}]\n" f"url = \"{url}\"\n"
    if token_env_var:
        block += f"bearer_token_env_var = \"{token_env_var}\"\n"

    content = ""
    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")

    pattern = re.compile(rf"\[mcp_servers\.{re.escape(server_name)}\][\s\S]*?(?=\n\[|\Z)")
    if pattern.search(content):
        content = pattern.sub(block, content)
    else:
        content = (content.rstrip() + "\n\n" + block).lstrip()

    config_path.write_text(content, encoding="utf-8")
    logger.info("codex_mcp_config_written", path=str(config_path))


def _normalize_mcp_url(url: str) -> str:
    """Ensure the MCP URL ends with /mcp (single trailing slash optional)."""
    if not url:
        return url
    normalized = url.rstrip("/")
    if normalized.endswith("/mcp"):
        return url if url.endswith("/") else normalized + "/"
    return normalized + "/mcp"
