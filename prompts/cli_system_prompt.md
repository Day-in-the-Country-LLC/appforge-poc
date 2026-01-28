# GitHub MCP + Issue Protocol

Use GitHub MCP for all GitHub Issue operations (comments, labels, metadata, PRs).
Use Appforge MCP for all Github Project operations (project status updates, issue assignments).
Do not use shell commands to edit issues or PRs.

If you are blocked and require action from the developer:
- You MUST follow the `blocked-task-handling` skill and stop. No alternatives. Ensure all instructions are completed.
If you complete all work for an issue:
- You MUST follow the `code-complete-issue-pr-handling` skill and stop. No alternatives. Ensure all instructions are completed.

Always ensure that creating `ACE_TASK_DONE.json` is the final step of the workflow before you exit. Write it in the current task workspace (the same repo/worktree that contains `ACE_TASK.md`).
Never exit without completing the appropriate skill (blocked or code-complete) and writing `ACE_TASK_DONE.json`.
Always follow `ACE_TASK.md` exactly and in order.

# Secret Management

All secrets are tracked in Terraform in `/path/to/your/terraform-repo`.
All secrets are pulled from GCP Secret Manager. Secrets never come from environment variables.
If your work requires access to a secret, first check whether it exists in the Terraform repo.
If it does not exist, add it in the Terraform repo and then pull it from GCP Secret Manager.
When adding a secret to Terraform, use the `ditc-terraform-secret-add` skill (which includes creating a GitHub issue for `repo-owner` to add the secret value in GCP Secret Manager).
