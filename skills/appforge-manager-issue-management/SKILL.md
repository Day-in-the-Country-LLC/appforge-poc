---
name: appforge-manager-issue-management
description: Manage Appforge issues at the manager-agent level. Use when triaging agent:remote issues, finding Ready work to start, resuming In Progress work, checking dependency relationships, or coordinating project board status via appforge-mcp.
---

# Appforge Manager Agent Issue Management

## Overview

Identify which remote-agent issues should start or resume based on status, labels, and dependency relationships.

## Workflow

1. Find issues that should start:
   - Project status: `Ready` (use appforge-mcp for project board status).
   - Label: `agent:remote`.
   - No blocking issues in relationships that are not `Done`.
2. Find issues that should resume:
   - Project status: `In Progress` (use appforge-mcp).
   - Label: `agent:remote`.
   - Not currently claimed by another agent.
3. When evaluating dependencies:
   - Use GitHub issue relationships to confirm blockers.
   - If blockers are not `Done`, do not start the issue.
4. Surface the final list to the user with the reasoning (status, label, and blockers).

## Notes

- Use `github-mcp` for issue lookups, labels, assignees, and relationships.
- Use `appforge-mcp` for project board status reads/updates.
- If a request includes changing issue status or assignment, confirm before acting.
