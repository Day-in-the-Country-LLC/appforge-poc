# Agent Issue Creation Guide for LLMs

This guide is for AI assistants (Copilot, Cascade, Codex, Claude) to help generate well-formatted issues that can be submitted to the **DITC TODO** org project in **Day-in-the-Country-LLC** for agent automation.

## Your Role

You are helping a human create issues that will be processed by an autonomous coding agent. You have access to MCP (Model Context Protocol) tools, including the **GitHub MCP server**, which allows you to directly create and update issues in GitHub repositories.

The downstream coding agent will:
1. Clone the repository
2. Create a feature branch
3. Implement the requested changes
4. Open a pull request
5. Update the issue status

Your job is to:
1. Help the human plan well-formed issues
2. Present the proposed issues for human approval
3. **After approval**, use the GitHub MCP server to create/update the issues directly
4. **After creation**, add the issue to the **Day-in-the-Country-LLC / DITC TODO** project board

## Issue Requirements

Every issue MUST include:

1. **Clear Title** - Specific, actionable, and descriptive
2. **Target Repository** - Explicitly stated in the issue body
3. **Detailed Description** - What needs to be done and why
4. **Acceptance Criteria** - Measurable, testable requirements
5. **Context** - Links, design docs, related issues, or background
6. **Dependencies** - Other issues or PRs that must be completed first

## Issue Format

Generate issues in this exact format:

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

Titles should follow this pattern:

```
[Action] [What] [Where/Context]
```

**Examples:**
- "Add dark mode support to frontend-repo"
- "Fix authentication bug in auth-service"
- "Implement caching layer in api-gateway"
- "Update dependencies in backend-repo"
- "Refactor state management in web-app"

## Agent Type Assessment

Determine where the issue should be processed:

- **`agent:remote`** - Can run on cloud VM (default for most issues)
  - Standard code changes, API work, frontend/backend features
  - No local filesystem, database, or machine-specific access needed
  - Examples: Add API endpoint, fix bug, implement feature, update dependencies

- **`agent:local`** - Requires local machine access
  - Database migrations involving local data
  - File system operations on local machine
  - Local service configurations (Redis, PostgreSQL, etc.)
  - Hardware or device-specific work
  - Examples: Migrate local Redis cache to cloud, export local database, configure local dev environment

**Default:** If unsure, use `agent:remote`. Most development work can run in the cloud.

## Difficulty Assessment

After generating the issue, assess its difficulty:

- **Easy** - Bug fixes, documentation, simple features, small refactors
- **Medium** - Feature implementations, integrations, moderate refactors
- **Hard** - Architecture changes, major refactors, complex algorithms

The human will add the appropriate `difficulty:*` label when submitting.

## Quality Checklist

Before presenting the issue to the human, verify:

- [ ] Title is clear and specific
- [ ] Target repository is explicitly named
- [ ] Description explains WHAT and WHY
- [ ] Acceptance criteria are testable and measurable
- [ ] No ambiguous language ("improve", "enhance", "make better")
- [ ] Criteria reference specific files or functions where relevant
- [ ] Implementation notes clarify any non-obvious approaches
- [ ] Related issues are linked for context
- [ ] An agent with no prior context could execute this

## Common Mistakes to Avoid

❌ **Don't:**
- Omit the target repository
- Use vague acceptance criteria ("should work", "looks good")
- Mix multiple unrelated tasks in one issue
- Assume the agent knows your codebase structure
- Leave out testing requirements
- Create issues with blocking dependencies

✅ **Do:**
- Be specific about file paths and function names
- Include exact error messages or reproduction steps
- Link to design docs or related issues
- Specify testing requirements clearly
- Mention any edge cases or special handling needed
- Provide examples of expected behavior

## Example: Well-Formed Issue

```markdown
## Target Repository
frontend-repo

## Description
Implement dark mode support for the application. Users should be able to toggle between light and dark themes via a settings menu. The user's preference should persist across sessions using localStorage.

This is a high-priority feature requested by multiple users and aligns with our Q1 roadmap.

## Acceptance Criteria
- [ ] Dark mode toggle added to settings menu (src/components/Settings.tsx)
- [ ] All components in src/components/ support dark mode styling
- [ ] Dark mode CSS variables defined in src/styles/theme.ts
- [ ] User preference persists in localStorage under key "theme"
- [ ] Default theme matches system preference (prefers-color-scheme)
- [ ] All tests pass: `npm test`
- [ ] No console errors or warnings in dark mode
- [ ] Storybook stories updated to show both light and dark variants

## Implementation Notes
- Use CSS custom properties (variables) for theme colors
- Follow existing color naming convention in theme.ts
- Test with both light and dark system preferences
- Ensure WCAG AA contrast ratios in both modes

## Related Issues
- Design mockups: #456
- Related: "Add theme switcher component" #123
- Blocked by: "Update design tokens" #789
```

## MCP Workflow

This agent has access to the GitHub MCP server for issue management:

1. **Plan** - Generate well-formed issue(s) based on user request
2. **Present** - Show the proposed issues to the human for review
3. **Await Approval** - Wait for explicit human approval before proceeding
4. **Create/Update** - Use the GitHub MCP server to create or update issues
5. **Add to Project Board** - Add all created/updated issues to the **Day-in-the-Country-LLC / DITC TODO** project board and set status to **Ready** (run the workflow if needed)
6. **Confirm** - Report back the created issue URLs, project board updates, and any applied labels

**Important:**
- Never create or modify GitHub issues without explicit human approval
- Use **dependencies** to establish issue relationships (e.g., blocked-by, depends-on) when creating or updating issues
- Always add created/updated issues to the **Day-in-the-Country-LLC / DITC TODO** project board

## Project Board Automation

Issues are added automatically via a GitHub Actions workflow:

- Workflow: `.github/workflows/add_issues_to_project.yml`
- Trigger: `issues` event (`opened`) for automatic adds
- Manual backfill: `workflow_dispatch` with optional `issue-number` (leave blank to add all issues; use `state=open` to limit)
- Project lookup: hardcoded to **Day-in-the-Country-LLC / DITC TODO** by title
- Status updates: workflow sets the **Status** field to **Ready**
- Required secrets:
  - `DITC_PROJECT_TOKEN` (PAT with org project write access + repo issue access + `read:project`)
- End-of-issue workflow: run the **Add issues to DITC TODO project** workflow after creating issues to ensure status is **Ready**

## For the Human

When you use this guide:

1. **Provide context** - Tell the AI what you want to build
2. **Review the generated issue(s)** - Make sure they are clear and complete
3. **Approve the plan** - Explicitly approve the issues for creation
4. **Verify** - The AI will create the issues via MCP and provide links

The AI will handle issue creation and labeling (`agent`, `agent:local` or `agent:remote`, `difficulty:*`). Project assignment happens automatically once the workflow and secrets are configured.

## Questions?

Refer to the main documentation:
- **For humans**: `docs/creating-agent-issues.md`
- **Issue protocol**: `docs/issue-readiness-protocol.md`
- **Difficulty guide**: `docs/difficulty-based-models.md`
