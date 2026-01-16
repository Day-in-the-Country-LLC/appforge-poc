# Agent Issue Creation Guide for LLMs

This guide is for AI assistants (Copilot, Cascade, Codex, Claude) to help create well-formatted issues for agent automation using the **GitHub MCP server**.

## Your Role

You are helping create an issue that will be processed by an autonomous coding agent. You will:
1. Use the GitHub MCP server tools to create the issue directly
2. Ensure the issue has the `agent` label and appropriate difficulty level
3. Set the issue status to "Ready" in the DITC TODO project

Your job is to create a clear, unambiguous issue that an agent can successfully execute.

## Issue Requirements

Every issue MUST include:

1. **Clear Title** - Specific, actionable, and descriptive
2. **Target Repository** - Explicitly stated in the issue body
3. **Detailed Description** - What needs to be done and why
4. **Acceptance Criteria** - Measurable, testable requirements
5. **Context** - Links, design docs, related issues, or background

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

## Creating Issues with GitHub MCP Server

Once you've formatted the issue, use the GitHub MCP server to create it:

### In Windsurf/VSCode

1. Open the MCP tools panel
2. Select "GitHub" → "create_issue"
3. Fill in the fields:
   - **owner**: Day-in-the-Country-LLC
   - **repo**: [target repository]
   - **title**: [issue title]
   - **body**: [formatted issue body]
   - **labels**: ["agent", "difficulty:medium"]

### In Copilot

Ask: "Create a GitHub issue with the following details..." and provide the formatted issue.

### In Claude Desktop

Use the GitHub tools panel to create the issue with the formatted content.

After creation, set the issue status to "Ready" in the DITC TODO project.

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

## How to Use This Guide

1. **Read the requirements** below to understand what makes a good agent issue
2. **Follow the format** section to structure your issue
3. **Use the GitHub MCP server** to create the issue directly in your IDE
4. **Check the quality checklist** before submitting
5. **Set labels and status** in the DITC TODO project

See `docs/github-mcp-setup.md` for GitHub MCP server configuration.

## Questions?

Refer to the main documentation:
- **For humans**: `docs/creating-agent-issues.md`
- **Issue protocol**: `docs/issue-readiness-protocol.md`
- **Difficulty guide**: `docs/difficulty-based-models.md`
