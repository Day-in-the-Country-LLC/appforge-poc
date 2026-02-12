# CLI Execution Model (Current + Legacy Notes)

This document used to describe tmux-backed execution. As of the current runtime,
CLI agents run as non-interactive subprocesses.

## 1) Where work runs

- Workspace root is `AGENT_WORKSPACE_ROOT` (default: `/tmp/agent-hq`).
- Each issue runs in `/tmp/agent-hq/worktrees/<repo>/<issue>/`.
- `ACE_TASK.md` is written before CLI spawn and must be present.

## 2) How the CLI is invoked

- `CliAgent.run()` builds a command from `CODEX_CLI_COMMAND` or `CLAUDE_CLI_COMMAND`.
- The command runs via a monitored `subprocess.Popen(..., cwd=<issue-workdir>)` loop.
- Both command templates must include `{prompt}`. Missing `{prompt}` is a hard error.
- Task prompt is loaded from `prompts/cli_task_prompt.md` (required; no fallback).
- For Codex, ACE prepends system prompt text to task prompt text.
- For Claude, task prompt is passed via `{prompt}` and system prompt is passed with `--append-system-prompt`.

## 3) Completion contract

- Success requires `ACE_TASK_DONE.json` in the issue worktree.
- Missing/invalid done file is a hard failure.
- CLI non-zero exit code is a hard failure.
- Timeout produces `task_wait_timeout`.
- ACE terminates the CLI process as soon as `ACE_TASK_DONE.json` is detected.

## 4) Env vars and MCP

Each subprocess run receives injected secrets/env (for example):

- `GITHUB_TOKEN`
- `OPENAI_API_KEY` (Codex path)
- `ANTHROPIC_API_KEY` (Claude path)

MCP config is generated per run:

- Codex: `~/.codex/config.toml`
- Claude: `<worktree>/.mcp.json` (git-ignored)

## 5) tmux status

- `src/ace/workspaces/tmux_ops.py` remains for legacy tooling.
- Current CLI execution path in `src/ace/agents/cli_agent.py` does not use tmux sessions.
