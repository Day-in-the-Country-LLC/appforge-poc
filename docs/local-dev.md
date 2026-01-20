# Local Development Guide

## Prerequisites

- Python 3.11+
- Git
- GitHub account with PAT (Personal Access Token)
- (Optional) ngrok for webhook testing

## Setup

### 1. Clone and Install

```bash
git clone <repo-url>
cd agentic-coding-engine
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 2. Environment Configuration

Create a `.env` file in the repo root:

```env
ENVIRONMENT=development
DEBUG=true

GITHUB_CONTROL_API_KEY=ghp_your_token_here
GITHUB_ORG=your-org
GITHUB_PROJECT_NAME=DITC TODO
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_READY_STATUS=Ready
GITHUB_AGENT_LABEL=agent
GITHUB_BASE_BRANCH=main
GITHUB_TOKEN_SECRET_NAME=github-control-api-key
GITHUB_TOKEN_SECRET_VERSION=latest
GITHUB_MCP_TOKEN_ENV=GITHUB_TOKEN
GITHUB_API_MAX_RETRIES=5
GITHUB_API_RETRY_BASE_SECONDS=1.0
GITHUB_API_RETRY_MAX_SECONDS=30.0
LANGSMITH_ENABLED=false
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_SECRET_NAME=LANGSMITH_ADS_OPTIMIZATION_KEY
LANGSMITH_SECRET_VERSION=latest
LANGSMITH_PROJECT=appforge-poc
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_LOG_PROMPTS=true
LANGSMITH_LOG_RESPONSES=true

APPFORGE_OPENAI_API_KEY=sk-...
CLAUDE_CODE_ADMIN_API_KEY=sk-...

GCP_PROJECT_ID=appforge-483920
GCP_CREDENTIALS_FILE=appforge-creds.json

AGENT_WORKSPACE_ROOT=/tmp/agent-hq
AGENT_ID=ace-dev
AGENT_EXECUTION_MODE=tmux
CODEX_CLI_COMMAND=codex --model {model}
CLAUDE_CLI_COMMAND=claude --model {model}
BLOCKED_ASSIGNEE=your-github-username

SERVICE_PORT=8080
SERVICE_HOST=0.0.0.0

POLLING_INTERVAL_SECONDS=60
TASK_AUTO_ADVANCE=true
TASK_POLL_INTERVAL_SECONDS=30
TASK_WAIT_TIMEOUT_SECONDS=0
TASK_NUDGE_ENABLED=true
TASK_NUDGE_AFTER_SECONDS=900
TASK_NUDGE_INTERVAL_SECONDS=300
TASK_NUDGE_MAX_ATTEMPTS=3
TASK_NUDGE_MAX_RESTARTS=1
TASK_NUDGE_MESSAGE=HEALTH_CHECK: please continue work on {task_id} ({task_title}). If blocked, post a BLOCKED comment and exit.
RESUME_IN_PROGRESS_ISSUES=true

CLEANUP_ENABLED=true
CLEANUP_INTERVAL_SECONDS=1800
CLEANUP_WORKTREE_RETENTION_HOURS=72
CLEANUP_TMUX_RETENTION_HOURS=12
CLEANUP_ONLY_DONE=true
CLEANUP_TMUX_ENABLED=true
```

## Running locally (no service)

Drain ready issues once (example: remote target, limit 1 issue):

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 1
```

Then configure the webhook in GitHub:
- Payload URL: `https://your-ngrok-url/webhook/github`
- Content type: `application/json`
- Events: `Issues`, `Issue comments`
- Secret: (use the value from `.env`)

### Manual Polling Trigger

```bash
curl -X POST http://localhost:8080/trigger/poll
```

### Process a Single Ticket

```bash
python -m ace.runners.worker 123
```

Where `123` is the GitHub issue number.

### Run the Issue Harness (End-to-End)

```bash
python scripts/run_issue_harness.py --owner <org> --repo <repo> --issue <number>
```

This will hit GitHub APIs, create comments, and open a PR when tasks complete.

Auto-select the first unblocked issue from the configured project:

```bash
python scripts/run_issue_harness.py --auto --target remote
```

## Development Workflow

### Running Tests

```bash
pytest tests/ -v
pytest tests/ -v --cov=ace
```

### Code Quality

```bash
black src/ tests/
ruff check src/ tests/
mypy src/
```

### Debugging

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or set `DEBUG=true` in `.env`.

### Capture tmux output

Grab the recent output from a tmux-backed CLI agent:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/dump_tmux_logs.py --repo appforge-poc --issue 123 --lines 200 --out /tmp/issue-123.log
```

If you already know the tmux session name, pass `--session <name>` instead of `--repo/--issue`.

## Common Tasks

### Add a New Agent Backend

1. Create `src/ace/agents/my_backend.py`
2. Implement the `BaseAgent` interface
3. Add selection logic in `select_backend` node
4. Test with `python -m ace.runners.worker <issue_number>`

### Add a New Graph Node

1. Add async function in `src/ace/orchestration/graph.py`
2. Add to workflow with `workflow.add_node()`
3. Connect edges appropriately
4. Test the full workflow

### Debug a Stuck Workflow

Check the logs:
```bash
tail -f /tmp/agent-hq/logs/issue-*.jsonl
```

Manually inspect the workspace:
```bash
ls -la /tmp/agent-hq/worktrees/
```

## Troubleshooting

### "GitHub token invalid"
- Verify `GITHUB_CONTROL_API_KEY` in `.env` or Secret Manager
- Check token has `repo` and `issues` scopes

### "MCP server unreachable"
- Ensure MCP server is running (if using local MCP)
- Verify MCP configuration for Codex/Claude CLI and `GITHUB_MCP_TOKEN_ENV` is set

### "Webhook not received"
- Verify ngrok is running
- Check GitHub webhook delivery logs
- Ensure webhook secret matches

### "Agent execution timeout"
- Check workspace logs in `/tmp/agent-hq/`
- Increase timeout in settings if needed
- Verify agent backend is installed

## Next Steps

- Set up GCP deployment (see `docs/gcp-deploy.md`)
- Implement real agent backends
- Add comprehensive tests
- Set up CI/CD pipeline
