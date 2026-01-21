---
name: project-architecture-doc
description: Create or update a shared `project_architecture.md` for a multi-repo project. Use when a request mentions project architecture docs, repo ownership boundaries, cross-repo responsibilities, or setting up shared project docs and symlinks. Often paired with new project setup work.
---

# Project Architecture Doc

## Overview

Create a single canonical `project_architecture.md` per project and symlink it into each repo so updates are centralized.

## Workflow

1. Gather the project name and the list of repos with their responsibilities.
2. Choose the shared path `~/.project-docs/<project>/project_architecture.md`.
3. Write the doc with these sections:
   - **Repos and Responsibilities**
   - **Cross-Repo Boundaries and Ownership**
   - **Guardrails**
4. Include global guardrails:
   - Use `uv` only for Python package management; do not use `pip` or `uv pip`.
   - Store secrets in GCP Secret Manager; manage via Terraform when possible.
   - Keep a single system of record per resource (avoid Terraform + app repos managing the same thing).
5. Reference shared infra repo `/Users/kristinday/ditc_terraform` in the doc, but do not place project docs inside it.
6. Symlink the shared doc into each project repo as `project_architecture.md`.
7. If the request includes broader new-project setup tasks, explicitly load the `new-project-setup` skill and follow its workflow.

## Template

Use this structure and adjust the repo list and details:

```
# <Project> Project Architecture

This document describes the <Project> multi-repo layout, what each repository owns, and where work should go.

## Repos and Responsibilities

### /Users/kristinday/<repo-name>
<Brief description>

Use this repo for:
- <Responsibilities>

Avoid here:
- <Boundaries>

### Infrastructure: /Users/kristinday/ditc_terraform
Shared Terraform repo for project-level infrastructure across all projects.

Use this repo for:
- GCP infrastructure and environment configuration.
- Secrets and project-level setup managed via Terraform.

Avoid:
- Managing the same resource in both Terraform and application repos.

## Cross-Repo Boundaries and Ownership

- **<Area>**: `<repo>`.

## Guardrails

- Use `uv` only for Python package management; do not use `pip` or `uv pip`.
- Store secrets in GCP Secret Manager; manage via Terraform when possible.
- Keep a single system of record per resource (avoid Terraform + application repos managing the same thing).
```
