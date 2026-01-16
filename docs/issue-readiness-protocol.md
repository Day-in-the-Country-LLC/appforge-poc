# Issue Readiness Protocol

The Agentic Coding Engine uses a combination of **project status** and **agent label** to determine which issues are ready for processing across your organization's projects.

## How It Works

### 1. Project Status: "Ready"
Issues in the **DITC TODO** org project with status **"Ready"** are candidates for agent processing.

The project status field indicates the issue's readiness state:
- **Ready** - Issue is prepared and ready for an agent to pick up
- **In Progress** - Agent is currently working on it
- **Blocked** - Agent is blocked, waiting for input
- **Done** - Agent completed the work
- **Other statuses** - Not ready for agents (Backlog, On Hold, etc.)

### 2. Agent Label: "agent"
Issues must have the **`agent`** label to be processed by the engine.

This label indicates:
- The issue is suitable for agent automation
- The issue has all necessary context and acceptance criteria
- The issue is not blocked by dependencies

### 3. Difficulty Label
Issues must also have **one** difficulty label:
- `difficulty:easy` - Simple tasks (uses Codex)
- `difficulty:medium` - Moderate complexity (uses Claude Haiku)
- `difficulty:hard` - Complex tasks (uses Claude Opus)

## Issue Readiness Checklist

Before marking an issue as "Ready", ensure:

- [ ] Issue has clear title and description
- [ ] Issue has `agent` label
- [ ] Issue has exactly one `difficulty:*` label
- [ ] Issue has project status set to "Ready"
- [ ] Issue specifies target repo (in body or via linked PR/branch)
- [ ] No blocking dependencies
- [ ] Acceptance criteria are clear

## Example Workflow

```
1. You create issue in DITC TODO project
   Title: "Add dark mode support to frontend-repo"
   Body: "Target repo: frontend-repo\n..."
   Labels: [agent, difficulty:medium]
   Status: Backlog

2. When ready, you update status to "Ready"

3. Engine polls and finds:
   - Status = "Ready" ✓
   - Label "agent" present ✓
   - Label "difficulty:medium" present ✓

4. Engine picks up the issue
   - Selects Claude Haiku backend
   - Creates branch in frontend-repo
   - Executes task
   - Opens PR

5. Engine updates status to "In Progress"

6. When complete, status becomes "Done"
```

## Configuration

The engine looks for:
- **Project**: DITC TODO (configurable via `GITHUB_PROJECT_NAME`)
- **Status**: "Ready" (configurable via `GITHUB_READY_STATUS`)
- **Label**: "agent" (configurable via `GITHUB_AGENT_LABEL`)
- **Difficulty**: One of `difficulty:easy`, `difficulty:medium`, `difficulty:hard`

## Multi-Repo Handling

Since DITC TODO is an org project tracking issues across multiple repos:

1. Each issue should specify its target repo in the issue body or description
2. The engine extracts the repo name and clones it
3. The agent works in that repo's context
4. The PR is opened in the target repo

Example issue body:
```
## Target Repository
frontend-repo

## Description
Add dark mode support...

## Acceptance Criteria
- [ ] Dark mode toggle in settings
- [ ] Persists user preference
- [ ] All components support dark mode
```

## Status Transitions

The engine manages status transitions automatically:

```
Ready (you set)
  ↓
In Progress (engine sets when claiming)
  ├─ Agent keeps "agent" label while working
  ├─ Agent posts updates in comments
  │
  ├→ Blocked (if agent needs input)
  │   ├─ Engine removes "agent" label
  │   ├─ Engine assigns to you
  │   ├─ Engine posts questions in comment
  │   ↓
  │   You respond:
  │   ├─ Re-add "agent" label
  │   ├─ Unassign yourself
  │   ├─ Post answer in comment
  │   ↓
  │   In Progress (engine resumes)
  │   ↓
  │   Done (when complete)
  │
  └→ Done (if successful)
     ├─ Engine removes "agent" label
     ├─ Engine posts PR link in comment
     └─ Status set to Done
```

## Blocked → Resume Workflow

When an agent gets blocked:

1. **Agent posts question** in comment with `BLOCKED` prefix
2. **Engine removes `agent` label** from issue
3. **Engine assigns issue to you** (kristinday)
4. **Engine sets status to Blocked**

When you're ready to help:

1. **Read the question** in the issue comments
2. **Post your answer** in a comment
3. **Re-add the `agent` label** to the issue
4. **Unassign yourself** from the issue
5. **Engine detects `agent` label re-added** and resumes

The engine will:
- Detect the label change
- Read your answer from comments
- Resume execution with your input
- Continue to completion or next blocker

## Monitoring

Check which issues are ready:

```bash
# Local development
grep "listing_issues_by_agent_label" /tmp/agent-hq/logs/*.log

# GCP Cloud Run
gcloud run services logs read agentic-coding-engine --region us-central1 | grep "listing_issues_by_agent_label"
```

## Troubleshooting

**Issue not being picked up?**
- Verify status is "Ready" (not "Backlog" or other)
- Verify `agent` label is present
- Verify `difficulty:*` label is present
- Check engine logs for filtering details

**Wrong repo being used?**
- Verify target repo is clearly specified in issue body
- Check that repo name matches exactly (case-sensitive)

**Status not updating?**
- Verify engine has permission to update project status
- Check that `GITHUB_CONTROL_API_KEY` has appropriate scopes
