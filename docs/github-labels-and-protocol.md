# GitHub Labels and Protocol

This document defines the state machine and communication protocol for the Agentic Coding Engine.

## Label State Machine

The engine uses GitHub labels to track issue state throughout the workflow:

### Core Labels

- **`agent:ready`** - Issue is ready for an agent to pick up
- **`agent:in-progress`** - Agent has claimed the issue and is working on it
- **`agent:blocked`** - Agent is blocked and waiting for human input
- **`agent:done`** - Agent has completed the work and opened a PR
- **`agent:failed`** - Agent encountered an error and could not complete the work

### Label Transitions

```
agent:ready
    ↓
agent:in-progress (when claimed)
    ↓
    ├→ agent:blocked (if questions arise)
    │   ↓
    │   agent:in-progress (when ANSWER: received)
    │   ↓
    │   agent:done
    │
    └→ agent:done (if successful)
    
    OR
    
    └→ agent:failed (if error)
```

## Communication Protocol

### Agent Asking for Help: `BLOCKED:` Comment

When an agent encounters a question or needs clarification, it posts a comment with the following format:

```
BLOCKED:

- Question 1: [specific question]
- Question 2: [specific question]
- ...

Reply with `ANSWER:` followed by your responses when ready.
```

The agent then:
1. Adds the `agent:blocked` label
2. Assigns the issue to the human reviewer
3. Waits for a response

### Human Responding: `ANSWER:` Comment

When you provide answers to blocked questions, post a comment:

```
ANSWER:

- Answer to question 1: [your answer]
- Answer to question 2: [your answer]
- ...
```

The engine will:
1. Detect the `ANSWER:` prefix
2. Remove the `agent:blocked` label
3. Resume execution with the provided answers

## Claim Comment Format

When an agent claims an issue, it posts a structured comment containing:

```
**Agent Claim**

- Agent ID: [agent_id]
- Host: [hostname]
- Branch: agent/[issue#]-[slug]
- Workspace: [path/to/workspace]
- Started: [ISO timestamp]
- Heartbeat: Updates posted at major milestones
```

This allows you to:
- Track which agent is working on the issue
- Jump into the workspace if needed
- Monitor progress via comments

## Example Workflow

1. **You label an issue `agent:ready`**
   ```
   Issue: "Add dark mode support"
   Labels: [agent:ready]
   ```

2. **Agent claims it**
   ```
   Comment: "Agent Claim - Agent ID: ace-default, Branch: agent/123-dark-mode, ..."
   Labels: [agent:in-progress]
   ```

3. **Agent gets blocked**
   ```
   Comment: "BLOCKED: - Should dark mode be opt-in or default?"
   Labels: [agent:blocked]
   Assigned to: [you]
   ```

4. **You answer**
   ```
   Comment: "ANSWER: - Dark mode should be opt-in with a toggle in settings"
   ```

5. **Agent resumes and completes**
   ```
   Comment: "PR opened: #456"
   Labels: [agent:done]
   ```

## Best Practices

- Keep questions specific and actionable
- Provide complete context in answers
- Use the exact `BLOCKED:` and `ANSWER:` prefixes for reliable detection
- Review PRs before merging to catch any issues
