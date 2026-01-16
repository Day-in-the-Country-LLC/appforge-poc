# GitHub MCP Server Setup

This guide explains how to set up the GitHub MCP (Model Context Protocol) server for **coding agents** to interact with GitHub directly.

## Architecture Overview

The ACE framework uses a **layered approach** for GitHub access:

| Component | GitHub Access Method | Purpose |
|-----------|---------------------|---------|
| **ACE Framework** | REST/GraphQL API (`api_client.py`, `projects_v2.py`) | Read project board, manage issue status, orchestration |
| **Coding Agents** | Official GitHub MCP Server | Create PRs, comment on issues, push code |

This separation ensures:
- The framework can query project board status (requires GraphQL, not available in MCP)
- Agents have scoped, secure access to GitHub operations they need
- No custom MCP server needed—agents use GitHub's official implementation

## GitHub MCP Server (For Agents)

The GitHub MCP server provides a standardized interface for AI tools (Codex CLI, Claude, etc.) to interact with GitHub.

**Benefits:**
- Works seamlessly in agent execution environments
- Standardized GitHub operations (issues, PRs, comments)
- Scoped permissions via PAT

## Installation

### Prerequisites

- GitHub Personal Access Token (PAT) with `repo` and `read:org` scopes
- MCP-compatible tool (Windsurf, VSCode with Copilot, etc.)

### Step 1: Create GitHub PAT

1. Go to GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)
2. Click "Generate new token (classic)"
3. Select scopes:
   - `repo` (all sub-options)
   - `read:org`
4. Generate and copy the token

### Step 2: Configure MCP Server

#### For Windsurf

Add to `.windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

#### For VSCode with Copilot

Add to `.vscode/settings.json`:

```json
{
  "github.copilot.advanced": {
    "debug.testOverrideProxyUrl": "http://localhost:3000",
    "debug.overrideChatApiUrl": "https://api.github.com"
  }
}
```

Or use the GitHub Copilot extension settings to configure MCP.

#### For Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

#### For ACE CLI/Tmux Agents (Codex or Claude CLI)

ACE launches CLI agents inside tmux sessions and injects a GitHub token into the
environment. Configure the MCP server in the CLI tool itself using the GitHub
MCP install guides, and ensure the token env var matches `GITHUB_MCP_TOKEN_ENV`
(default: `GITHUB_TOKEN`).

Key env vars used by ACE:
- `GITHUB_MCP_TOKEN_ENV` (default `GITHUB_TOKEN`)
- `GITHUB_CONTROL_API_KEY` (fallback for REST operations)
- `GITHUB_TOKEN_SECRET_NAME` / `GITHUB_TOKEN_SECRET_VERSION` when using Secret Manager

ACE automatically sets these inside the tmux session.

### Step 3: Verify Installation

Test the MCP server connection:

```bash
# In Windsurf or VSCode, open the MCP tools panel
# You should see GitHub tools available:
# - create_issue
# - update_issue
# - list_issues
# - search_issues
# - get_issue
# - add_issue_comment
# - etc.
```

## Creating Issues

### Using the MCP Server

Once configured, you can create issues directly through your IDE:

1. **In Windsurf/VSCode:** Use the MCP tools panel or mention `@github` in chat
2. **In Copilot:** Ask Copilot to create an issue with specific details
3. **In Claude:** Use the GitHub tools in the tools panel

### Issue Format

When creating issues, follow this format:

```
Title: [Clear, actionable title]

Target Repository: [repo-name]

Description:
[What needs to be done]

Acceptance Criteria:
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

Labels: agent, difficulty:medium
```

### Example

**Create an issue for caching:**

```
Title: Add Redis caching to API endpoints

Target Repository: api-gateway

Description:
Implement Redis caching for frequently accessed endpoints to reduce latency and database load.

Acceptance Criteria:
- [ ] Redis client configured in app
- [ ] Cache decorator implemented for GET endpoints
- [ ] Cache invalidation on POST/PUT/DELETE
- [ ] Tests pass with caching enabled
- [ ] Performance metrics show improvement

Labels: agent, difficulty:medium, performance, backend
```

## Issue Labels

Use these labels for agent-driven issues:

### Agent Labels

- **`agent`** - Issue is ready for agent automation
- **`agent:local`** - Issue requires local machine access (e.g., local databases, file migrations)
- **`agent:remote`** - Issue can be processed by cloud VM agents

**Note:** If neither `agent:local` nor `agent:remote` is specified, the issue will be processed by any available agent (backwards compatible behavior).

### Difficulty Labels

- **`difficulty:easy`** - Simple changes, well-defined scope
- **`difficulty:medium`** - Moderate complexity, some research needed
- **`difficulty:hard`** - Complex changes, significant refactoring

### Category Labels

- `performance` - Performance optimization
- `backend` - Backend service
- `frontend` - Frontend application
- `bug` - Bug fix
- `feature` - New feature

## Project Status

Issues in the **DITC TODO** org project use status fields:

- **Ready** - Issue is ready for agent to claim
- **In Progress** - Agent is working on it
- **Blocked** - Agent needs human input
- **Done** - Issue completed, PR merged

## Workflow

1. **Create issue** using MCP server with `agent` label
2. **Set status** to "Ready" in DITC TODO project
3. **Agent claims** the issue and starts work
4. **Agent creates PR** when work is complete
5. **Status updates** to "Done" when PR is merged

## Troubleshooting

### "MCP server not found" error

**Solution:** Verify MCP configuration file exists and is valid JSON. Restart your IDE.

### "Permission denied" error

**Solution:** Verify GitHub PAT has `repo` and `read:org` scopes. Regenerate if needed.

### "Issue creation failed" error

**Solution:** Ensure:
- Repository exists
- Target repository is in the organization
- PAT has access to the repository

### "Cannot find DITC TODO project" error

**Solution:** Verify the project exists in the organization and the PAT has `read:org` scope.

## Security

- **Never commit** the GitHub PAT to version control
- **Use environment variables** or IDE secret storage
- **Rotate regularly** - Regenerate PAT quarterly
- **Monitor usage** - Check GitHub audit logs for token activity
- **Limit scope** - Only grant `repo` and `read:org` (no admin access)

## References

- [GitHub MCP Server Documentation](https://github.com/github/github-mcp-server)
- [MCP Protocol Specification](https://modelcontextprotocol.io/)
- [GitHub Personal Access Tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
