# Local Development Guide

## Prerequisites

- Python 3.12+
- uv (https://astral.sh/uv)
- Git
- GitHub account with PAT (Personal Access Token)

## Setup

### 1. Clone and Install

```bash
git clone <repo-url>
cd appforge-poc
uv sync --dev
```

### 2. Environment Configuration

Create a `.env` file in the repo root:

```env
DEBUG=false

GITHUB_TOKEN=github_token_example
GITHUB_ORG=your-org
GITHUB_PROJECT_NAME=your-project
GITHUB_READY_STATUS=Ready
GITHUB_AGENT_LABEL=agent
GITHUB_TOKEN_SECRET_NAME=github-control-api-key
GITHUB_TOKEN_SECRET_VERSION=latest
GITHUB_MCP_TOKEN_ENV=GITHUB_TOKEN
GITHUB_API_MAX_RETRIES=5
GITHUB_API_RETRY_BASE_SECONDS=1.0
GITHUB_API_RETRY_MAX_SECONDS=30.0
LANGSMITH_ENABLED=false
LANGSMITH_API_KEY=langsmith_example
LANGSMITH_SECRET_NAME=LANGSMITH_ADS_OPTIMIZATION_KEY
LANGSMITH_SECRET_VERSION=latest
LANGSMITH_PROJECT=appforge-coding-engine
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_LOG_PROMPTS=true
LANGSMITH_LOG_RESPONSES=true

APPFORGE_OPENAI_API_KEY=openai_example
CLAUDE_CODE_ADMIN_API_KEY=anthropic_example

GCP_PROJECT_ID=your-gcp-project-id
GCP_CREDENTIALS_FILE=gcp-credentials.json

AGENT_WORKSPACE_ROOT=/tmp/agent-hq
AGENT_ID=ace-dev
AGENT_EXECUTION_MODE=tmux
CODEX_CLI_COMMAND=codex --ask-for-approval never --full-auto --sandbox danger-full-access --model {model}
CLAUDE_CLI_COMMAND=claude --permission-mode dontAsk --dangerously-skip-permissions --model {model}
BLOCKED_ASSIGNEE=your-github-username

TASK_WAIT_TIMEOUT_SECONDS=0
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
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 1 --secrets-backend env
```

### Run the Issue Harness (End-to-End)

```bash
uv run python scripts/run_issue_harness.py --owner <org> --repo <repo> --issue <number> --secrets-backend env
```

This will hit GitHub APIs, create comments, and open a PR when tasks complete.

Auto-select the first unblocked issue from the configured project:

```bash
uv run python scripts/run_issue_harness.py --auto --target remote --secrets-backend env
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
- Verify `GITHUB_TOKEN` in `.env` or Secret Manager
- Check token has `repo` and `issues` scopes

### "MCP server unreachable"
- Ensure MCP server is running (if using local MCP)
- Verify MCP configuration for Codex/Claude CLI and `GITHUB_MCP_TOKEN_ENV` is set

### "Agent execution timeout"
- Check workspace logs in `/tmp/agent-hq/`
- Increase timeout in settings if needed
- Verify agent backend is installed

## Next Steps

- Set up GCP deployment (see `docs/gcp-deploy.md`)
- Implement real agent backends
- Add comprehensive tests
- Set up CI/CD pipeline
