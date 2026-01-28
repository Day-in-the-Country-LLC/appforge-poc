# Appforge Project Architecture

This document describes a multi-repo layout, what each repository owns, and where work should go.

## Repos and Responsibilities

### appforge-poc
Autonomous coding framework for app building.
- Core agent orchestration and app-building automation.
- Agent workflows, tooling, and framework-level improvements.

### appforge-mcp
Agent tools for Appforge processes.
- MCP server tools, transports, and integrations used by Appforge agents.

### Infrastructure (optional)
Shared Terraform repo for project-level infrastructure across all projects.
- GCP infrastructure and environment configuration.
- Secrets and project-level setup managed via Terraform.

## Cross-Repo Boundaries and Ownership

- **Autonomous coding framework**: `appforge-poc`.
- **Agent tools (MCP)**: `appforge-mcp`.
- **Prompt optimization**: `appforge-ads-optimization`.
- **Marketing backend**: `irlsc-marketing`.
- **Project-level infra**: Terraform repo (if used).

## Guardrails

- Use `uv` only for Python package management; do not use `pip` or `uv pip`.
- Store secrets in a secrets manager; manage via Terraform when possible.
- Keep a single system of record per resource (avoid Terraform + application repos managing the same thing).
