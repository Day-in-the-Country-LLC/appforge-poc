# Difficulty-Based Model Selection

The Appforge Coding Engine automatically selects the appropriate coding model based on issue difficulty labels. This allows you to optimize cost and performance by using faster/cheaper models for simple tasks and more capable models for complex work.

## How It Works

When an issue is picked up by the engine:

1. The `select_backend` workflow node examines the issue labels
2. It looks for a `difficulty:*` label
3. Based on the difficulty, it selects the appropriate backend and model
4. The agent executes using that model

## Difficulty Levels

### Easy (`difficulty:easy`)
- **Backend**: Codex
- **Model**: gpt-5.1-codex
- **Use for**: Bug fixes, simple refactoring, documentation updates, straightforward feature additions
- **Cost**: Lower
- **Speed**: Faster

### Medium (`difficulty:medium`)
- **Backend**: Claude
- **Model**: claude-haiku-4-5
- **Use for**: Feature implementations, moderate refactoring, API integrations, moderate complexity tasks
- **Cost**: Medium
- **Speed**: Medium

### Hard (`difficulty:hard`)
- **Backend**: Claude
- **Model**: claude-opus-4-1
- **Use for**: Complex architecture changes, multi-file refactoring, intricate logic, performance optimization
- **Cost**: Higher
- **Speed**: Slower but more capable

## Configuration

You can customize the backend and model for each difficulty level via environment variables:

```env
# Easy difficulty
DIFFICULTY_EASY_BACKEND=codex
DIFFICULTY_EASY_MODEL=gpt-5.1-codex

# Medium difficulty
DIFFICULTY_MEDIUM_BACKEND=claude
DIFFICULTY_MEDIUM_MODEL=claude-haiku-4-5

# Hard difficulty
DIFFICULTY_HARD_BACKEND=claude
DIFFICULTY_HARD_MODEL=claude-opus-4-1
```

In GCP, these are stored in Secret Manager and injected at runtime.

## Labeling Issues

When creating or updating issues in your project, add **exactly one** difficulty label:

```
Issue: "Fix typo in README"
Labels: [agent:remote, difficulty:easy]

Issue: "Add OAuth integration"
Labels: [agent:remote, difficulty:medium]

Issue: "Refactor authentication system"
Labels: [agent:remote, difficulty:hard]
```

## Error Handling

If an issue has a target label (`agent:remote` or `agent:local`) but **missing a difficulty label**, the engine will:

1. Log a warning
2. Fall back to the default model (easy/Codex)
3. Continue processing

This prevents the workflow from failing, but you should add the appropriate difficulty label to ensure optimal model selection.

## Best Practices

- **Be honest about difficulty** - Mislabeling can lead to task failures or wasted resources
- **Start conservative** - If unsure, label as `difficulty:hard` rather than `difficulty:easy`
- **Monitor results** - Track which difficulty levels succeed/fail to calibrate your labeling
- **Adjust as needed** - If easy tasks are failing, move them to medium; if medium tasks are too slow, move to hard

## Example Workflow

```
1. You create issue: "Add dark mode support"
2. You label it: difficulty:medium
3. Engine picks it up
4. select_backend node chooses: Claude + claude-haiku-4-5
5. Agent executes the task
6. PR opens with changes
```

## Monitoring

Check logs to see which models are being selected:

```bash
# Local development
tail -f /tmp/agent-hq/logs/issue-*.jsonl | grep backend_selected

# Runtime logs
grep backend_selected /tmp/agent-hq/logs/*.jsonl
```

Output example:
```
backend_selected issue=123 backend=claude model=claude-haiku-4-5
```
