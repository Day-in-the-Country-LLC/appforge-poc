"""CLI to run the agent pool with an optional max-issues limit (testing-friendly)."""

from __future__ import annotations

import argparse
import asyncio

from ace.config.logging import configure_logging
from ace.config.settings import get_settings, set_settings_overrides
from ace.runners.agent_pool import AgentTarget, get_pool


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent pool once (drain mode).")
    parser.add_argument(
        "--secrets-backend",
        choices=["secret-manager", "env"],
        default="secret-manager",
        help="Where to load secrets from (default: secret-manager).",
    )
    parser.add_argument(
        "--target",
        choices=[t.value for t in AgentTarget],
        default=AgentTarget.REMOTE.value,
        help="Which issues to process (remote/local). Default: remote",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=None,
        help="Maximum issues to process this run (0 or omitted = unlimited).",
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=30,
        help="Seconds between drain checks. Default: 30",
    )
    return parser.parse_args()


async def _run_once(target: AgentTarget, max_issues: int | None, check_interval: int) -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug)

    pool = get_pool(target)
    if max_issues is not None:
        pool.set_max_issues_per_run(max_issues)

    result = await pool.run_until_empty(check_interval=check_interval)
    # Log-friendly print; structlog already captured inside pool
    print(result)


def main() -> None:
    args = _parse_args()
    set_settings_overrides(secrets_backend=args.secrets_backend)
    target = AgentTarget(args.target)
    max_issues = args.max_issues
    if max_issues is not None and max_issues < 0:
        raise SystemExit("--max-issues must be >= 0")
    asyncio.run(_run_once(target, max_issues, args.check_interval))


if __name__ == "__main__":
    main()
