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
4. For `ditc_terraform` update issues, add an explicit instruction to run the `terraform-apply` skill at the end of the work.
5. When a new secret is added to Secret Manager via Terraform, create a follow-up issue assigned to `klday` to add the secret value as a version, including this command in the issue: `printf %s \"$SECRET_VALUE\" | gcloud secrets versions add SECRET_NAME --data-file=-`.
6. Choose the execution label: `developer`, `agent:local`, or `agent:remote` based on who/where the work must happen.
7. Apply exactly one difficulty label: `difficulty:easy`, `difficulty:medium`, or `difficulty:hard`.
8. Create the issue using `github-mcp` and set project status to `Backlog` using `appforge-mcp`.

## References

- Read `references/github_issue_creation_spec.md` for the canonical ACE requirements and label rules.
