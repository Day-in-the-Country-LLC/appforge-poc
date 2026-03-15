# GitHub Webhooks (Org CLI)

This file documents how to create the org-level webhook for the Project V2 board using the GitHub CLI.

## Requirements

- GitHub Projects **V2** only. Classic Projects (V1) do not emit `projects_v2_item`.
- GitHub CLI (`gh`) authenticated with org admin rights.
- Listener URL available at `<listener-base-url>/github/webhooks`.

## Events

- `projects_v2_item`
- `issue_comment`
- `pull_request`

## Create The Org Webhook (CLI)

Use the script below (it wraps `gh api` and enforces the required events).

```bash
scripts/create_org_webhook.sh \
  --org <org> \
  --url <listener-base-url>/github/webhooks \
  --secret <GITHUB_WEBHOOK_SECRET>
```

Notes:

- Payload content type is set to `application/json`.
- If you need to rotate the secret, re-run the script with the new secret value.
