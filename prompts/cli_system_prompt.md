# GitHub MCP + Issue Protocol

Use GitHub MCP for all GitHub Issue operations (comments, labels, metadata, PRs).
Use Appforge MCP for all Github Project operations (project status updates, issue assignments).
Do not use shell commands to edit issues or PRs.

If you are blocked and require action from the developer:
- Follow the `blocked-task-handling` skill.
If you complete all work for an issue:
- Follow the `claude-cli-pr-completion` skill.

Always ensure that creating `ACE_TASK_DONE.json` is the final step of the workflow before you exit.

# Secret Management

All secrets are tracked in Terraform in `/Users/kristinday/ditc_terraform`.
All secrets are pulled from GCP Secret Manager. Secrets never come from environment variables.
If your work requires access to a secret, first check whether it exists in the Terraform repo.
If it does not exist, add it in the Terraform repo and then pull it from GCP Secret Manager.
When adding a secret to Terraform, use the `ditc-terraform-secret-add` skill (which includes creating a GitHub issue for `klday` to add the secret value in GCP Secret Manager).
