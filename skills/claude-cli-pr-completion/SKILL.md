---
name: claude-cli-pr-completion
description: Use when all work from ACE_TASK.md is finished to create a PR via GitHub MCP, move the issue to In review with proper issue comments via Appforge MCP, assign to klday, and write ACE_TASK_DONE.json. Also defer to blocked-task-handling when developer input is required.
---

# Claude CLI PR Completion

## Goal
Finalize completed work with a PR and issue comments/status updates.

## Workflow

### A) Work completed

1) Create PR (GitHub MCP)
- Create a PR from the feature branch to `qa`.
- Title: `Agent: <issue title>`.
- Body must include:
  - Summary of work completed
  - Suggested test steps
- Add the same agent label as the issue (`agent:remote` or `agent:local`) to the PR.

2) Add issue comment (GitHub MCP)
- Add a comment to the issue with the PR link and a brief summary of the changes.

3) Set issue status to In review (Appforge MCP)
- Update the project status to `In review`.

4) Assign issue
- Assign the issue to `klday` via GitHub MCP.

5) Write `ACE_TASK_DONE.json`
- Create the file in the repo root (same directory as `ACE_TASK.md`).
- Use this exact JSON shape:

```json
{
  "task_id": "task-1",
  "summary": "<short summary>",
  "files_changed": ["<path>", "<path>"] ,
  "commands_run": ["<command>"]
}
```

5) Exit
- Exit only after `ACE_TASK_DONE.json` is written.

### B) Blocked

- If any required information is missing, follow `blocked-task-handling` and stop.

## Notes
- Use GitHub MCP for PR creation, comments, and assignment.
- Use Appforge MCP for project status updates and checking blockers in issue relationships.
- Do not open a PR if the issue is blocked - exit this skill and use `blocked-task-handling` instead.
