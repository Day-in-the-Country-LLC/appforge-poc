# Agent Issue Creation Guide for LLMs

This guide is for AI assistants (Copilot, Cascade, Codex, Claude) to help generate well-formatted issues that can be submitted to the **DITC TODO** org project for agent automation.

## Your Role

You are helping a human create an issue that will be processed by an autonomous coding agent. The agent will:
1. Clone the repository
2. Create a feature branch
3. Implement the requested changes
4. Open a pull request
5. Update the issue status

Your job is to help the human write a clear, unambiguous issue that an agent can successfully execute.

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

## For the Human

When you use this guide:

1. **Provide context** - Tell the AI what you want to build
2. **Review the generated issue** - Make sure it's clear and complete
3. **Add labels** - Include `agent` and `difficulty:*` labels
4. **Set status** - Mark as "Ready" in the DITC TODO project
5. **Copy and paste** - Submit the issue to the project

The AI will help you write issues that agents can execute successfully.

## Questions?

Refer to the main documentation:
- **For humans**: `docs/creating-agent-issues.md`
- **Issue protocol**: `docs/issue-readiness-protocol.md`
- **Difficulty guide**: `docs/difficulty-based-models.md`
