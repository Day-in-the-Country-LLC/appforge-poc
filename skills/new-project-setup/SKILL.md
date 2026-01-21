---
name: new-project-setup
description: Set up a new multi-repo project, including shared project documentation, repo boundaries, and initial architecture scaffolding. Use when creating a new project, adding repos to a project, or organizing ownership across repos. Often requires creating a `project_architecture.md` and symlinking it into all repos.
---

# New Project Setup

## Overview

Initialize a new project across multiple repos and standardize documentation and ownership boundaries from day one.

## Workflow

1. Gather inputs: project name, repo list, responsibilities, and any shared infra constraints.
2. Create the shared docs path `~/.project-docs/<project>/project_architecture.md`.
3. Explicitly load and follow the `project-architecture-doc` skill to draft the architecture doc.
4. Symlink the shared `project_architecture.md` into each project repo.
5. Ensure the shared infra repo `/Users/kristinday/ditc_terraform` is referenced in the doc but does not contain per-project docs.
6. Apply global guardrails:
   - Use `uv` only for Python package management; do not use `pip` or `uv pip`.
   - Store secrets in GCP Secret Manager; manage via Terraform when possible.
   - Keep a single system of record per resource (avoid Terraform + app repos managing the same thing).

## Notes

- If the user asks only for the architecture doc, stop after completing that and do not perform additional repo setup.
- If repo-specific standards are provided (frameworks, ownership rules, tooling), include them in the doc under the relevant repo section.
