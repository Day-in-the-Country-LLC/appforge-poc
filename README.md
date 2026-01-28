# Appforge Coding Engine (ACE)

An open-source orchestration layer that routes GitHub issues to coding agents, manages
status transitions, and provides tooling for local or remote execution.

## What this repo includes

- Agent pool orchestration and scheduling
- GitHub issue/project integration
- CLI-based agents (Codex/Claude) and MCP hooks
- Optional GCP Secret Manager integration

## Quick start

1. Install Python 3.12+ and `uv`.
2. Copy `.env.example` to `.env` and fill in values.
3. Run: `uv sync --dev`
4. Start a run: `uv run python scripts/run_agent_pool.py --target local --max-issues 1 --secrets-backend env`

For a detailed setup guide, see `docs/ONBOARDING.md`.
