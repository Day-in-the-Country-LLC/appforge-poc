# Architecture Overview

## High-Level Design

The Agentic Coding Engine is a multi-repo orchestration system that:

1. **Monitors GitHub issues** for tasks labeled `agent:ready`
2. **Orchestrates agents** to execute work in isolated workspaces
3. **Manages state** through GitHub labels and comments
4. **Opens PRs** with completed work
5. **Handles blockers** via a human-in-the-loop protocol

## Core Components

### 1. GitHub Integration (`src/ace/github/`)

- **`api_client.py`** - GitHub REST and GraphQL API client using httpx
- **`projects_v2.py`** - GitHub Projects V2 GraphQL operations (read/update project board status)
- **`issue_queue.py`** - High-level API for issue operations (list, claim, comment, label, create PR)
- **`status_manager.py`** - Manages issue status transitions and agent label handling

The ACE framework communicates with GitHub via REST/GraphQL APIs for orchestration tasks (reading project board, updating status). **Coding agents** spawned by ACE use the official GitHub MCP server directly for their GitHub operations.

### 2. Agent Layer (`src/ace/agents/`)

- **`base.py`** - Abstract base class defining the agent interface
  - `plan()` - Generate execution plan
  - `run()` - Execute task in workspace
  - `respond_to_answer()` - Resume after blocked question answered
- **`policy.py`** - Safety constraints and execution rules injected into every task

Agents are pluggable implementations (Codex CLI, Claude, etc.) that conform to this interface.
The CLI path runs inside tmux and reads `ACE_TASK.md` for detailed task instructions.

### 3. Orchestration (`src/ace/orchestration/`)

- **`state.py`** - Pydantic state model for the workflow
- **`graph.py`** - LangGraph workflow definition
- **`task_manager.py`** - Sequential task planning, instruction generation, and task tracking

The workflow is a state machine with these nodes:

```
fetch_candidates
    ↓
claim_issue
    ↓
hydrate_context
    ↓
select_backend
    ↓
run_agent
    ↓
evaluate_result
    ├→ handle_blocked (if questions)
    ├→ open_pr (if success)
    └→ post_failure (if error)
    ↓
mark_done
```

In CLI/tmux mode, `run_agent` coordinates sequential tasks inside a single worktree.
Each task writes `ACE_TASK.md` for the coding CLI and completes by dropping
`ACE_TASK_DONE.json`. When all tasks are complete, the manager opens the PR.

### 4. Service Layer (`src/ace/runners/`)

- **`service.py`** - FastAPI service with:
  - Health check endpoint
  - GitHub webhook receiver
  - Manual poll trigger
- **`worker.py`** - Entrypoint for processing a single ticket
- **`scheduler.py`** - (Optional) Polling loop for Cloud Scheduler

### 5. Workspace Management (`src/ace/workspaces/`)

- **`git_ops.py`** - Git operations (clone, worktree, branch, push)
- **`tmux_ops.py`** - tmux session/window management (used in CLI/tmux mode)

### 6. Configuration (`src/ace/config/`)

- **`settings.py`** - Environment-based configuration
- **`logging.py`** - Structured logging setup

## Data Flow

### Ticket Pickup Flow

```
GitHub Issue (agent:ready)
    ↓
Webhook / Polling
    ↓
Service receives event
    ↓
Worker spawned with issue_number
    ↓
LangGraph executes workflow
    ↓
Worktree + branch created
    ↓
Task plan + instructions written (ACE_TASK.md)
    ↓
CLI agent executes in tmux (sequential tasks)
    ↓
PR opened, labels updated
    ↓
GitHub Issue (agent:done)
```

### Blocked Flow

```
Agent encounters question
    ↓
Posts BLOCKED: comment
    ↓
Sets agent:blocked label
    ↓
Assigns to human
    ↓
Human replies ANSWER:
    ↓
Webhook detects ANSWER:
    ↓
Worker resumes with answer
    ↓
Agent continues execution
```

## Deployment Model

### Local Development

```
FastAPI service (uvicorn)
    ↓
GitHub webhook (via ngrok or similar)
    ↓
Worker processes issues locally
```

### GCP Cloud Run

```
Cloud Run Service
    ├→ Webhook receiver (always running)
    └→ Polling trigger (Cloud Scheduler every 1-5 min)
    ↓
Cloud Run Job (per ticket)
    ├→ Secrets from Secret Manager
    ├→ Workspace in ephemeral storage
    └→ Agent execution
```

## Security Model

1. **Layered GitHub access**:
   - ACE framework uses GitHub REST/GraphQL APIs with a service token for orchestration
   - Coding agents use GitHub MCP server with scoped permissions for their operations
2. **Secrets in Secret Manager** - Never in code or environment
3. **Branch protection** - Agents never push to main
4. **PR-only workflow** - All changes go through review
5. **Policy injection** - Safety rules prepended to every task

## Multi-Repo Orchestration

The engine is repo-agnostic:

- Issues are tracked in a central "orchestration repo"
- Agents receive context about target repos
- Agents handle cloning and working in project-specific repos
- Engine only manages state and coordination

This allows:
- Single orchestration point for multiple projects
- Agents to work on different repos in parallel
- Flexible agent implementations per project type
