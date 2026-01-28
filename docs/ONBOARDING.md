# Onboarding Guide

This guide walks through getting Appforge Coding Engine (ACE) running locally and explains how to set up the two MCP servers used by the CLI coding agents inside tmux sessions.

## Prerequisites

- Python 3.12+
- uv (`pip install uv`)
- Git
- GitHub Personal Access Token (PAT)
- Access to a GCP project if using Secret Manager

## 1) Install and sync dependencies

```bash
git clone <repo-url>
cd appforge-poc
uv sync --dev
```

## 2) Configure environment

Copy `.env.example` to `.env` and fill in values.

Key values to confirm:

- `GITHUB_TOKEN` (required when running with `--secrets-backend env`)
- `GITHUB_ORG` / `GITHUB_PROJECT_NAME`
- `GITHUB_READY_STATUS=Ready`
- `GITHUB_LOCAL_AGENT_LABEL=agent:local`
- `GITHUB_REMOTE_AGENT_LABEL=agent:remote`
- `APPFORGE_MCP_ENABLED` / `APPFORGE_MCP_URL` (optional)
- `GCP_PROJECT_ID` + `GCP_CREDENTIALS_FILE` (required for Secret Manager mode)

## 3) MCP servers (required for CLI coding agents)

ACE uses two MCP servers:

1) **GitHub MCP server** — used by Codex/Claude CLI agents inside tmux sessions.
2) **Appforge MCP server** — optional for manager/queue operations (recommended for remote runs).

ACE can work with a single org-wide GitHub Project board that tracks issues across many repos. In this model, you typically run the coding CLI inside a specific project repo and use the `github_issue_creation` skill to open issues that get placed on the org-wide board.

### 3.1 GitHub MCP server (for CLI agents)

The CLI agents (Codex/Claude) use GitHub MCP to create PRs, comment, and update issues. You must configure this in your Codex/Claude CLI settings.

Minimum requirements:

- Your CLI tool must have an MCP config that points to the GitHub MCP server.
- `GITHUB_TOKEN` must be available to the CLI process (ACE injects it into tmux sessions).
- `GITHUB_MCP_TOKEN_ENV` in `.env` should remain `GITHUB_TOKEN`.

See `docs/github-mcp-setup.md` for exact MCP config examples.

### 3.2 Appforge MCP server (optional, recommended)

The appforge-mcp server is used to efficiently list **Ready + remote** issues with blocker awareness. This is optional but recommended for remote runs.

Appforge MCP exists to fill project board management gaps that GitHub MCP doesn't cover (especially around Projects V2 workflows). If you rely on a single org-wide board across many repos, Appforge MCP provides the missing project-board operations that keep statuses, blockers, and readiness aligned.

Important:

- We **do not** publish our current deployment endpoint.
- You must deploy your own appforge-mcp instance and point ACE at it.

Steps:

1. Clone the `appforge-mcp` repo.
2. Deploy it (Cloud Run, VM, or your preferred runtime).
3. Set:
   - `APPFORGE_MCP_ENABLED=true`
   - `APPFORGE_MCP_URL=<your-deployment-url>`
   - `APPFORGE_MCP_SERVER_NAME=appforge-mcp-server`

If you do not deploy it, ACE will fall back to direct GitHub API calls.

## 4) Run locally

Example run (local target):

```bash
uv run python scripts/run_agent_pool.py --target local --max-issues 1 --secrets-backend env
```

Example run (remote target, using Secret Manager):

```bash
uv run python scripts/run_agent_pool.py --target remote --max-issues 1 --secrets-backend secret-manager
```

## 5) Install required skills for CLI agents

The coding CLIs look for skills in these directories:

- `~/.codex/skills/<skill-name>/SKILL.md`
- `~/.claude/skills/<skill-name>/SKILL.md`

For this repo, at minimum install:

- `blocked-task-handling`
- `code-complete-issue-pr-handling`

You can copy the skill folders from the repo `skills/` directory into each home directory:

```bash
mkdir -p ~/.codex/skills ~/.claude/skills
cp -R skills/blocked-task-handling ~/.codex/skills/
cp -R skills/blocked-task-handling ~/.claude/skills/
cp -R skills/code-complete-issue-pr-handling ~/.codex/skills/
cp -R skills/code-complete-issue-pr-handling ~/.claude/skills/
```

If you maintain skills elsewhere, ensure the same names and folder structure exist under both `~/.codex/skills` and `~/.claude/skills`.

## 6) Verify CLI agent behavior

When a tmux session starts, the coding CLI will:

- Read `ACE_TASK.md` in the worktree
- Execute the instructions
- Use **blocked-task-handling** when blocked
- Use **code-complete-issue-pr-handling** when done
- Write `ACE_TASK_DONE.json` in the same worktree that contains `ACE_TASK.md`

## References

- `docs/github-mcp-setup.md`
- `docs/gcp-deploy.md`
- `docs/local-dev.md`
