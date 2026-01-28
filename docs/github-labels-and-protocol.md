# GitHub Labels and Protocol

This document describes the labels and status protocol used by the Appforge Coding Engine.

## Labels

### Target Labels (required)

- `agent:remote` — issue can run on cloud/VM agents
- `agent:local` — issue requires local machine access

### Difficulty Labels (required)

- `difficulty:easy`
- `difficulty:medium`
- `difficulty:hard`

### Optional Label

- `agent` — optional helper label used by some workflows when marking blocked/resume. The engine does **not** require it to pick up work, but some blocked flows may remove/re-add it for visibility.

## Status (Project Board)

The engine uses project status to track state:

- **Ready** — issue is ready to process
- **In Progress** — agent is working
- **Blocked** — agent needs input
- **Done** — work completed

## Blocked Protocol

When an agent needs input, it posts a comment like:

```
**BLOCKED - Agent Needs Input**

1. Question one
2. Question two
```

The engine will:
1. Set project status to **Blocked**
2. Assign the issue to the human reviewer
3. Optionally remove the `agent` label (if your workflow uses it)

### Resume

To resume work:
1. Reply with answers in a comment
2. Set status back to **Ready** (or **In Progress** if you want immediate resumption)
3. Ensure the target label (`agent:remote` / `agent:local`) is still present
4. Optionally re-add the `agent` label (if your workflow uses it)

## Example Workflow

1. **You prepare an issue**
   - Labels: `agent:remote`, `difficulty:medium`
   - Status: Backlog
2. **You mark it Ready**
3. **Engine claims it**
   - Status: In Progress
4. **Engine completes work**
   - PR opened
   - Status: Done
