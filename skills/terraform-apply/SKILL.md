---
name: terraform-apply
description: Apply Terraform changes safely when Terraform configuration updates are made (.tf files, modules, or terraform.lock.hcl). Use when finishing Terraform update tasks and you need to run init/plan/apply and capture results.
---

# Terraform Apply

## Overview

Apply Terraform changes in a controlled, repeatable way after configuration updates, capturing plan/apply results for the task.

## Workflow

1. Identify the Terraform root
   - Locate the directory with the root module (typically contains `main.tf` and/or backend/provider blocks).
   - If multiple roots exist, apply only the one(s) touched by the update.

2. Prepare the workspace
   - Ensure required credentials, environment variables, and backend config are available.
   - Run `terraform init` (use `-upgrade` only if module/provider versions changed).
   - Run `terraform validate` and stop on errors.

3. Plan the change
   - Run `terraform plan -out=tfplan` and review for unexpected changes.
   - If the plan is empty, note it and stop without applying.

4. Apply the change
   - Run `terraform apply tfplan` to apply the reviewed plan.
   - Use `-auto-approve` only when a non-interactive apply is explicitly required.

5. Capture results
   - Save key plan/apply output details in the task (resource changes, outputs, or any manual follow-ups).
   - If apply fails, stop and surface the error immediately.
