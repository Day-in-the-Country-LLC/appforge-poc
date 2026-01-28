---
name: github-issue-creation
description: Create GitHub issues using the Appforge Coding Engine (ACE) framework. Use when opening issues for ACE-managed repos, especially when you must choose the correct repo via project_architecture.md, create modular issues with dependency relationships, apply required labels, set difficulty, and put new issues in Backlog.
---

# GitHub Issue Creation (ACE = Appforge Coding Engine)

## Overview

Follow the ACE issue creation workflow to open issues and set project board status across repos.

## Your Role

You are helping a human create issues that will be processed by an autonomous coding agent.
You must:
1. Help the human plan well-formed issues.
2. Present the proposed issues for human approval.
3. After approval, use GitHub MCP to create/update issues.
4. After creation, add issues to the **your-org / your-project** project board.

## Workflow

1. Locate the target repo by reading its `project_architecture.md` to confirm where the issue belongs.
2. Create a discrete, modular issue focused on a single task or goal.
3. Capture dependencies in the issue description and use GitHub issue relationships to link them.
4. For `your-terraform-repo` update issues, add an explicit instruction to run the `terraform-apply` skill at the end of the work.
5. When a new secret is added to Secret Manager via Terraform, create a follow-up issue assigned to `repo-owner` to add the secret value as a version, including this command in the issue: `printf %s \"$SECRET_VALUE\" | gcloud secrets versions add SECRET_NAME --data-file=-`.
6. Choose the execution label: `developer`, `agent:local`, or `agent:remote` based on who/where the work must happen.
7. Apply exactly one difficulty label: `difficulty:easy`, `difficulty:medium`, or `difficulty:hard`.
8. Present the issue(s) to the human for approval.
9. After approval, create/update the issue(s) using `github-mcp`.
10. Set project status to `Backlog` using `appforge-mcp`.

## Issue Requirements

Every issue MUST include:
1. Clear title (specific, actionable)
2. Target repository (explicitly stated in body)
3. Detailed description (what + why)
4. Acceptance criteria (testable)
5. Context (links, docs, background)
6. Dependencies (blocked-by/depends-on)

## Choosing the Right Repository (Multi-Repo Guidance)

- Prefer one repo per issue. Split cross-repo work into sub-issues.
- Defaults by area:
  - Infra/Cloud: `your-terraform-repo`
  - MCP/tools: `<project>-mcp`
  - Frontend: `<project>-web` or `<project>-frontend`
  - Orchestration/Backend: main service repo
  - Data/DB migrations: owning service repo

## Issue Format (Use This)

```markdown
## Target Repository
[exact-repo-name]

## Description
[2-3 sentences describing what needs to be done]

[Additional context, background, or rationale]

## Acceptance Criteria
- [ ] Specific, testable requirement 1
- [ ] Specific, testable requirement 2
- [ ] Specific, testable requirement 3
- [ ] Code follows project conventions
- [ ] Tests pass (if applicable)
- [ ] No console errors or warnings

## Implementation Notes
[Optional: specific files to modify, patterns to follow, or constraints]

## Related Issues
[Links to related issues, PRs, or documentation]
```

## Title Format

`[Action] [What] [Where/Context]`

Examples:
- "Add dark mode support to frontend-repo"
- "Fix authentication bug in auth-service"
- "Implement caching layer in api-gateway"

## Agent Type Assessment

- `agent:remote` (default): standard code changes, no local access needed
- `agent:local`: needs local machine access (DB, files, device)
- `developer`: human-only actions (DNS, vendor dashboards)

## Difficulty Assessment

- Easy: small fixes/docs
- Medium: feature work/integrations
- Hard: architecture changes/complex work

## Quality Checklist

- Title is clear and specific
- Target repo is named explicitly
- Description explains what + why
- Acceptance criteria are testable
- No ambiguous language
- Dependencies linked
- Implementation notes included if non-obvious

## Common Mistakes to Avoid

- Missing target repo
- Vague acceptance criteria
- Multiple unrelated tasks in one issue
- Missing tests or verification steps

## Example Issue

```markdown
## Target Repository
frontend-repo

## Description
Implement dark mode support for the application. Users should be able to toggle between light and dark themes via a settings menu. The preference should persist across sessions.

## Acceptance Criteria
- [ ] Dark mode toggle added to settings menu (src/components/Settings.tsx)
- [ ] Theme variables defined in src/styles/theme.ts
- [ ] Preference persists in localStorage under key "theme"
- [ ] All tests pass: `npm test`
- [ ] No console errors or warnings
```

## MCP Workflow (Must Follow)

1. Plan issues.
2. Present to human.
3. Await explicit approval.
4. Create/update via GitHub MCP.
5. Add to project board + set status.
6. Confirm back with links and labels applied.

## Project Board Automation

- Workflow: `.github/workflows/add_issues_to_project.yml`
- Trigger: `issues` event (`opened`)
- Manual backfill: `workflow_dispatch` with optional `issue-number`
- Project lookup: hardcoded to **your-org / your-project** by title
- Status updates: sets **Status** to **Ready**
- Required secret: `DITC_PROJECT_TOKEN`

## References

- Read `references/github_issue_creation_spec.md` for the canonical ACE requirements and label rules.
