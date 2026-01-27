---
name: appforge-cli-issue-management
description: Manage Appforge issues from the CLI coding agent. Use when changing issue status to Blocked or In review, unclaiming issues, assigning to repo-owner, commenting with developer requests, or creating PRs and linking them back to issues.
---

# Appforge CLI Coding Agent Issue Management

## Overview

Update issue status and communication for work that needs developer input or review.

## Workflow

### A) Developer input needed

1. Change issue status to `Blocked` (use appforge-mcp for project board status).
2. Assign the issue to `repo-owner`.
3. Comment with clear, actionable details about what the developer needs to do.
4. Unclaim the issue so another agent can pick it up after the developer updates status back to `In Progress`.

### B) Ready for review

1. Change issue status to `In review` (use appforge-mcp).
2. Create a PR with:
   - Summary of work completed.
   - Suggested test steps.
3. Comment on the issue with a link to the PR.
4. Assign the issue to `repo-owner`.
5. Send a Twilio text to the configured review number with the PR link and a brief summary (skip for now; TODO until the campaign is approved).

## Notes

- Use `github-mcp` for issue comments, assignments, and PR creation.
- Use `appforge-mcp` for project board status reads/updates.
- If the PR already exists, link it and skip creating a duplicate.
