# Production Readiness Checklist

Use this checklist before deploying ACE to production. Treat it as a living document.

## Core Reliability
- [ ] GitHub API calls use retry/backoff and respect rate limits.
- [ ] CLI agent failures always surface a reason in issue comments.
- [ ] Task nudges and restarts are tuned for your expected task durations.
- [ ] Worktree cleanup strategy defined (retain vs. prune after completion).

## Observability
- [ ] Structured logs include issue number, repo, task id, and session name.
- [ ] Aggregate metrics exist for success/failure, duration, and nudge counts.
- [ ] Alerts cover repeated task failures, repeated nudges, and stuck sessions.

## Security
- [ ] Tokens are loaded from Secret Manager only (no plaintext env in prod).
- [ ] MCP config files are ignored and never committed.
- [ ] PAT scopes are least-privilege (repo/workflow/gist only as needed).

## CI/CD
- [ ] CI runs lint + tests on every PR.
- [ ] Release pipeline builds and deploys with environment-specific config.
- [ ] Dependency updates are automated and monitored.

## Runbook
- [ ] Clear instructions for handling blocked issues and retries.
- [ ] Manual override steps for killing/restarting tmux sessions.
- [ ] Backup/restore steps for workspaces if retention is required.
