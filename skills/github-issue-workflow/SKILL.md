---
name: github-issue-workflow
description: Create and mark agent-ready GitHub issues for the your-project project (labels, status, template, readiness checks).
---

# GitHub Issue Workflow

Use this skill when creating or validating agent-ready issues, or when asked to mark issues Ready/Blocked/Done.

## Quick checklist

- Issue includes target repo
- Labels: `agent` + exactly one `difficulty:*`
- Project status: `Ready` (when prepared)
- Acceptance criteria are clear and testable

## Template

Use `AGENT_ISSUE_TEMPLATE.md` for the canonical format. If you need a minimal inline template:

```markdown
## Target Repository
repo-name

## Description
What needs to be done and why

## Acceptance Criteria
- [ ] Requirement 1
- [ ] Requirement 2
```

## Readiness + lifecycle

- Ready: `agent` label + difficulty label + project status `Ready`
- Blocked: remove `agent`, set status `Blocked`, ask a clear question in comments
- Resume: add `agent` label, unassign yourself, status back to `Ready`

## References

Open these when you need more detail:
- `docs/issue-creation-workflow.md`
- `docs/issue-readiness-protocol.md`
- `docs/creating-agent-issues.md`
- `AGENT_ISSUE_TEMPLATE.md`
