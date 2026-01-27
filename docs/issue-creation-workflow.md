# Issue Creation Workflow

This document explains how agents and developers create issues for the your-project project using the GitHub MCP server.

## Overview

Instead of using a custom Python SDK, we leverage the **GitHub MCP (Model Context Protocol) server** to enable AI tools to create and manage GitHub issues. This approach is simpler, more maintainable, and works seamlessly across all development environments.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Development Environment (Windsurf, VSCode, Copilot, Claude) │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  GitHub MCP Server         │
        │  (create_issue, etc.)      │
        └────────────────┬───────────┘
                         │
                         ▼
        ┌────────────────────────────┐
        │  GitHub API                │
        │  (your-org)  │
        └────────────────┬───────────┘
                         │
                         ▼
        ┌────────────────────────────┐
        │  your-project Project         │
        │  (Issues & Status)         │
        └────────────────────────────┘
```

## Workflow

### 1. Issue Creation

**Actor:** Developer or LLM (via MCP server)

**Steps:**
1. Use GitHub MCP server tools in your IDE
2. Provide issue details (title, description, target repo, labels)
3. MCP server creates issue in GitHub
4. Issue appears in your-project project

**Tools:**
- Windsurf/VSCode: MCP tools panel
- Copilot: Chat interface
- Claude Desktop: GitHub tools panel

### 2. Issue Readiness

**Actor:** Developer

**Steps:**
1. Review issue for clarity and completeness
2. Add a target label: `agent:remote` or `agent:local`
3. Add `difficulty:*` label (easy, medium, hard)
4. Set project status to "Ready"

**Labels:**
- `agent:remote` - Ready for cloud/VM automation
- `agent:local` - Requires local-machine access
- `difficulty:easy` - Simple, well-defined
- `difficulty:medium` - Moderate complexity
- `difficulty:hard` - Complex, significant work

### 3. Agent Execution

**Actor:** Autonomous coding agent

**Steps:**
1. Agent claims issue (sets status to "In Progress")
2. Agent clones repo and creates feature branch
3. Agent implements changes
4. Agent creates pull request
5. Agent updates issue status to "Done" when PR merges

### 4. Issue Lifecycle

```
Created
   ↓
Ready (target label + difficulty label)
   ↓
In Progress (agent claimed)
   ↓
Blocked (if agent needs help)
   ↓
Done (PR merged)
```

## Setup

### Prerequisites

1. **GitHub PAT** with `repo` and `read:org` scopes
2. **MCP-compatible tool** (Windsurf, VSCode, Copilot, Claude)
3. **GitHub MCP server** configured

### Configuration

See `docs/github-mcp-setup.md` for detailed setup instructions.

## Issue Format

All issues should follow this structure:

```markdown
## Target Repository
[exact-repo-name]

## Description
[What needs to be done and why]

## Acceptance Criteria
- [ ] Specific requirement 1
- [ ] Specific requirement 2
- [ ] Specific requirement 3
- [ ] Tests pass
- [ ] No console errors

## Implementation Notes
[Optional: specific files, patterns, constraints]

## Related Issues
[Links to related issues or PRs]
```

## Labels

### Status Labels (Project)
- **Ready** - Issue ready for agent to claim
- **In Progress** - Agent is working on it
- **Blocked** - Agent needs human input
- **Done** - Issue completed, PR merged

### Target Labels
- `agent:remote` - Ready for cloud/VM automation
- `agent:local` - Requires local-machine access

### Category Labels
- `difficulty:easy` - Simple changes
- `difficulty:medium` - Moderate complexity
- `difficulty:hard` - Complex changes
- `bug` - Bug fix
- `feature` - New feature
- `performance` - Performance optimization
- `backend` - Backend service
- `frontend` - Frontend application

## Best Practices

### For Issue Creation

1. **Be specific** - Include file paths, function names, exact requirements
2. **Include context** - Link to design docs, related issues, PRs
3. **Define success** - Write testable acceptance criteria
4. **Avoid ambiguity** - Don't use vague terms like "improve" or "enhance"
5. **Consider scope** - Keep issues focused and completable in one PR

### For Agents

1. **Claim clearly** - Set status to "In Progress" when starting work
2. **Communicate** - Add comments if blocked or need clarification
3. **Test thoroughly** - Ensure all acceptance criteria are met
4. **Document changes** - Summarize what was done in PR description
5. **Update status** - Mark as "Done" when PR merges

## Troubleshooting

### MCP Server Not Available

**Problem:** GitHub tools not showing in IDE

**Solution:**
1. Verify MCP configuration file exists
2. Check GitHub PAT is valid and has correct scopes
3. Restart IDE
4. See `docs/github-mcp-setup.md`

### Issue Creation Failed

**Problem:** Error when creating issue

**Solution:**
1. Verify target repository exists
2. Ensure PAT has `repo` scope
3. Check repository is in the organization
4. Verify your-project project exists

### Agent Can't Find Issue

**Problem:** Agent doesn't see issue in project

**Solution:**
1. Verify issue has `agent:remote` or `agent:local` label
2. Check project status is set to "Ready"
3. Ensure issue is in your-project project
4. Verify agent has access to repository

## References

- `AGENT_ISSUE_TEMPLATE.md` - Issue format guide for LLMs
- `docs/github-mcp-setup.md` - MCP server configuration
- `docs/creating-agent-issues.md` - Guide for humans creating issues
- [GitHub MCP Server](https://github.com/github/github-mcp-server)
- [MCP Protocol](https://modelcontextprotocol.io/)
