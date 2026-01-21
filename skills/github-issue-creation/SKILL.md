---
name: github-issue-creation
description: Create GitHub issues using the ACE framework. Use when opening issues for ACE-managed repos, especially when you must choose the correct repo via project_architecture.md, create modular issues with dependency relationships, apply required labels, set difficulty, and put new issues in Backlog.
---

# GitHub Issue Creation

## Overview

Follow the ACE issue creation workflow to open issues and set project board status across repos.

## Workflow

1. Locate the target repo by reading its `project_architecture.md` to confirm where the issue belongs.
2. Create a discrete, modular issue focused on a single task or goal.
3. Capture dependencies in the issue description and use GitHub issue relationships to link them.
4. Choose the execution label: `developer`, `agent:local`, or `agent:remote` based on who/where the work must happen.
5. Apply exactly one difficulty label: `difficulty:easy`, `difficulty:medium`, or `difficulty:hard`.
6. Create the issue using `github-mcp` and set project status to `Backlog` using `appforge-mcp`.

## References

- Read `references/github_issue_creation_spec.md` for the canonical ACE requirements and label rules.
