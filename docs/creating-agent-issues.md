# Creating Issues for Agents

This guide walks you through creating and labeling issues in the **DITC TODO** org project so agents can pick them up and execute work.

## Quick Checklist

Before marking an issue as "Ready", ensure:

- [ ] Issue has clear, descriptive title
- [ ] Issue body includes target repository name
- [ ] Issue has `agent` label
- [ ] Issue has exactly one `difficulty:*` label
- [ ] Issue has clear acceptance criteria
- [ ] No blocking dependencies
- [ ] Project status is set to "Ready"

## Step-by-Step: Creating an Agent Issue

### 1. Create the Issue

In the **DITC TODO** org project:
- Click **New issue**
- Write a clear title (e.g., "Add dark mode support to frontend-repo")
- Fill in the description

### 2. Issue Body Template

Use this template for consistency:

```markdown
## Target Repository
[repo-name]

## Description
[What needs to be done?]

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Additional Context
[Any relevant links, design docs, or context]
```

**Example:**

```markdown
## Target Repository
frontend-repo

## Description
Implement dark mode support for the application. Users should be able to toggle between light and dark themes, with their preference persisted in local storage.

## Acceptance Criteria
- [ ] Dark mode toggle in settings menu
- [ ] All components support dark mode styling
- [ ] User preference persists across sessions
- [ ] No console errors in dark mode
- [ ] Tests pass for dark mode functionality

## Additional Context
Design mockups: [link]
Related issue: #456
```

### 3. Add Labels

**Required labels:**

- **`agent`** - Marks the issue for agent processing
- **`difficulty:easy`** - Simple tasks (bug fixes, small features, docs)
- **`difficulty:medium`** - Moderate complexity (feature implementations, integrations)
- **`difficulty:hard`** - Complex tasks (architecture changes, major refactors)

**Optional labels:**
- `bug` - Bug fix
- `enhancement` - New feature
- `documentation` - Documentation update
- `performance` - Performance improvement

**Example label set:**
```
agent, difficulty:medium, enhancement
```

### 4. Set Project Status

In the **DITC TODO** project board:
- Set the issue status to **"Ready"** when it's prepared for an agent
- Leave as **"Backlog"** if it's not ready yet

## Difficulty Selection Guide

### Easy (`difficulty:easy`)
**Use for:**
- Bug fixes
- Documentation updates
- Simple refactoring
- Straightforward feature additions
- Typo fixes
- Configuration changes

**Examples:**
- "Fix typo in README"
- "Update API documentation"
- "Add missing error handling"
- "Update dependency version"

**Model:** Codex (gpt-5.1-codex) - Fast & cost-effective

### Medium (`difficulty:medium`)
**Use for:**
- Feature implementations
- API integrations
- Moderate refactoring
- Database migrations
- UI component additions
- Test coverage improvements

**Examples:**
- "Add OAuth integration"
- "Create user profile page"
- "Implement caching layer"
- "Add email notifications"

**Model:** Claude Haiku (claude-haiku-4-5) - Balanced capability & cost

### Hard (`difficulty:hard`)
**Use for:**
- Architecture changes
- Major refactors (>5 files, >100 lines per file)
- Complex algorithm implementations
- Performance optimizations
- Security improvements
- Multi-component integrations

**Examples:**
- "Refactor authentication system"
- "Implement microservices architecture"
- "Optimize database queries"
- "Redesign state management"

**Model:** Claude Opus (claude-opus-4-5) - Most capable

## Common Mistakes to Avoid

❌ **Don't:**
- Create issues without target repository
- Use multiple difficulty labels (pick ONE)
- Forget to add the `agent` label
- Set status to "Ready" before issue is complete
- Include vague acceptance criteria
- Create issues with blocking dependencies

✅ **Do:**
- Be specific about what needs to be done
- Include clear acceptance criteria
- Specify the target repository explicitly
- Use one difficulty label
- Always add the `agent` label
- Only mark "Ready" when the issue is fully prepared

## What Happens After You Mark "Ready"

1. **Engine polls** the DITC TODO project
2. **Engine finds** your issue with status "Ready" and `agent` label
3. **Engine selects model** based on `difficulty:*` label
4. **Engine claims** the issue (sets status to "In Progress")
5. **Agent executes** the work in the target repository
6. **Agent opens PR** with changes
7. **Agent updates status** to "Done" or "Blocked"

## If Agent Gets Blocked

When an agent needs your input:

1. **Issue status** becomes "Blocked"
2. **Agent label** is removed
3. **Issue is assigned** to you
4. **Question posted** in comments

**To resume:**
1. Read the question in comments
2. Post your answer in a comment
3. **Re-add the `agent` label**
4. **Unassign yourself**
5. Engine detects the label and resumes

## If Agent Fails

When an agent encounters an error:

1. **Issue status** becomes "Blocked"
2. **Agent label** is removed
3. **Issue is assigned** to you
4. **Error details** posted in comments

**To retry:**
1. Review the error
2. Fix the issue (update description, clarify requirements, etc.)
3. **Re-add the `agent` label**
4. **Unassign yourself**
5. Engine will retry

## Example: Complete Workflow

### You create issue:
```
Title: Add dark mode support to frontend-repo
Status: Backlog
Labels: (none yet)
```

### You prepare and mark ready:
```
Title: Add dark mode support to frontend-repo
Status: Ready
Labels: agent, difficulty:medium, enhancement
Body: [Complete with target repo, description, acceptance criteria]
```

### Engine picks it up:
```
Status: In Progress
Agent: Selects Claude Haiku (medium difficulty)
```

### Agent works and completes:
```
Status: Done
Labels: (agent label removed)
Comment: PR #789 opened
```

### You review and merge:
```
Review PR in frontend-repo
Merge when ready
Issue remains Done
```

## Tips for Success

1. **Be specific** - The more detail, the better the agent can execute
2. **Test locally first** - If you can reproduce the issue, the agent can too
3. **Link related issues** - Help the agent understand context
4. **Use templates** - Consistency helps agents understand patterns
5. **Review PRs carefully** - Agents are powerful but not perfect
6. **Provide feedback** - If an agent fails, explain why so it can improve
7. **Start with easy tasks** - Build confidence before complex work
8. **Monitor the first few** - Watch how agents handle your issues

## Questions?

Check the other documentation:
- **Issue Readiness Protocol**: `docs/issue-readiness-protocol.md`
- **Difficulty-Based Models**: `docs/difficulty-based-models.md`
- **Architecture**: `docs/architecture.md`
