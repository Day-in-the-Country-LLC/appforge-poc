# Architecture Overview

## High-Level Design

The Appforge Coding Engine is a multi-repo orchestration system that:

1. **Monitors GitHub issues** in the project board with status **Ready** and a target label (`agent:remote` or `agent:local`)
2. **Orchestrates agents** to execute work in isolated workspaces
3. **Manages state** through project status, labels, and comments
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

- **`cli_agent.py`** - Spawns tmux sessions for Codex/Claude CLI
- **`policy.py`** - Safety constraints and execution rules injected into every task
- **`types.py`** - Agent result status/types

The CLI path runs inside tmux and reads `ACE_TASK.md` for detailed task instructions.

### 3. Orchestration (`src/ace/orchestration/`)

- **`state.py`** - Pydantic state model for the workflow
- **`graph.py`** - LangGraph workflow definition

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
    ├→ blocked handling (if questions)
    └→ success/failure handling
```

In CLI/tmux mode, `run_agent` coordinates a single work item inside a worktree.
Instructions are written to `ACE_TASK.md` and the coding CLI completes by dropping
`ACE_TASK_DONE.json`. PR creation and issue/project updates are handled by the
CLI via the required completion/blocked skills.

### 4. Runners/Scheduler (`src/ace/runners/`)

- **`agent_pool.py`** - Concurrent agent manager
- **`scheduler.py`** - Daily trigger (optional)
- **`worker.py`** - Single-ticket entrypoint (legacy helper)

### 5. Workspace Management (`src/ace/workspaces/`)

- **`git_ops.py`** - Git operations (clone, worktree, branch, push)
- **`tmux_ops.py`** - tmux session/window management (used in CLI/tmux mode)

### 6. Configuration (`src/ace/config/`)

- **`settings.py`** - Environment-based configuration
- **`logging.py`** - Structured logging setup

## Data Flow

### Ticket Pickup Flow

```
GitHub Issue (Status: Ready + target label)
    ↓
Scheduled/CLI run (agent pool)
    ↓
Worker spawned with issue_number
    ↓
LangGraph executes workflow
    ↓
Worktree + branch created
    ↓
Instructions written (ACE_TASK.md)
    ↓
CLI agent executes in tmux
    ↓
PR opened, status updated
    ↓
GitHub Issue (Status: Done)
```

### Blocked Flow

```
Agent encounters question
    ↓
Posts BLOCKED comment
    ↓
Sets status to Blocked
    ↓
Assigns to human
    ↓
Human replies with answers
    ↓
Status set back to Ready/In Progress
    ↓
Worker resumes with answer
```

## Deployment Model (non-HTTP)

Run the agent pool as a scheduled/CLI drain (e.g., cron/Cloud Scheduler) to process ready issues, then exit. No FastAPI/HTTP surface is present.

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
