# Webhook Listener (GitHub App)

This document describes the **listener** that receives GitHub webhooks. The recommended form is a **GitHub App**, which provides scoped permissions, better auditability, and easier rotation than a classic PAT.

## What the Listener Is

The listener is an HTTP service that:

1. Verifies webhook signatures.
2. Parses the GitHub event type.
3. Filters to the exact triggers we care about.
4. Kicks off orchestration for the relevant issue or PR.

The listener **does not** create webhooks itself unless you explicitly build a provisioning flow. Webhooks are configured in GitHub (UI/CLI/API) and pointed at the listener endpoint.

## Why a GitHub App

- Scoped, minimal permissions.
- Webhook secret per app installation.
- Supports org-wide installs and repo-level scoping.
- Easy to rotate/disable without changing user PATs.

## GitHub App Configuration

Create a GitHub App (org or user account) with:

- **Webhook URL**: `<listener-base-url>/github/webhooks`
- **Webhook secret**: a random string you generate and store securely
- **Permissions**:
  - Issues: Read (to inspect issue + PR metadata)
  - Pull requests: Read (to confirm PR + fetch context)
  - Projects: Read (Projects V2 item changes)
  - Metadata: Read
- **Subscribe to events**:
  - `Projects v2 item`
  - `Issue comment`

Install the app on the org or specific repos that map to your Project V2 board.

## How To Get The App ID And Private Key

You’ll get these from the GitHub App settings page.

Steps:

1. GitHub → Settings → Developer settings → GitHub Apps.
2. Create a new app (or select an existing one).
3. On the app’s settings page:
   - **App ID**: shown near the top of the page. Use this value for `GITHUB_APP_ID`.
   - **Private key**: click **Generate a private key**, then download the `.pem`. Use the file contents for `GITHUB_APP_PRIVATE_KEY`.

Tip: Store the private key as a secret and load it from your secret manager at runtime.

## OAuth Callback / User Authorization (Not Needed For Webhooks)

If you only need webhooks, you **do not** need OAuth user authorization.

When creating the GitHub App:

- Leave **Callback URL** blank.
- Disable **User authorization** (OAuth flow) if the UI offers a checkbox.

Security still comes from:

- The webhook secret signature check (`X-Hub-Signature-256`).
- The GitHub App credentials (App ID + private key).

## Where The Webhook Secret Comes From

You choose the webhook secret. It should be a random, high-entropy string (32+ bytes).

Examples:

- `openssl rand -hex 64` (recommended)
- `python - <<'PY'\nimport secrets\nprint(secrets.token_hex(64))\nPY`

Tips:

- Prefer generating in a password manager or locally and pasting once.
- Avoid storing the secret in shell history or files.

Use the same value in:

- GitHub App webhook secret
- Your listener’s `GITHUB_WEBHOOK_SECRET`

## Required Env / Secrets

- `GITHUB_APP_ID`
- `GITHUB_APP_PRIVATE_KEY`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_ORG`
- `GITHUB_PROJECT_NAME`
- `APPFORGE_MCP_URL` (required; webhook processing fails if missing)

Optional (notifications):

- `SLACK_BOT_TOKEN` (or `SLACKBOT_TOKEN`)
- `SLACK_CHANNEL_ID`

## Project Name

`GITHUB_PROJECT_NAME` must match the **exact title** of the GitHub Project V2 board (case sensitive).

## GitHub App Settings Checklist

Use this to confirm the app is configured correctly.

### Event Subscriptions

1. Go to GitHub → Settings → Developer settings → GitHub Apps → *your app*.
2. Scroll to **Webhook**.
3. Under **Subscribe to events**, ensure these are checked:
   - `Issue comment`
   - `Projects v2 item`

### Permissions

1. In the same app settings page, go to **Permissions & events**.
2. Set these permissions:
   - **Issues**: Read
   - **Pull requests**: Read
   - **Projects**: Read
   - **Metadata**: Read (required by GitHub; cannot be removed)
3. Click **Save changes** if prompted.

### Installation Scope

Install the app on the org or on the specific repos tied to the Project V2 board.
If the board is org‑level, org installation is recommended.

## Listener Endpoint Contract

- Path: `/github/webhooks`
- Method: `POST`
- Headers:
  - `X-GitHub-Event`
  - `X-Hub-Signature-256`
  - `X-GitHub-Delivery`
- Body: JSON payload for the subscribed event

## Event Routing (Minimal v1)

### 1) Project item status change

- Event: `projects_v2_item`
- Trigger when status transitions:
  - `Backlog` → `Ready`
  - `Blocked` → `In Progress`
- Ignore all other status transitions and any project item that does not belong
  to the configured `GITHUB_PROJECT_NAME`.

### 2) PR comment added

- Event: `issue_comment`
- Trigger when:
  - `action == created`
  - `issue.pull_request` exists
- Ignore all other issue comment events.

## Expected Handler Behavior

- **Validate signature**: reject any request without a valid signature.
- **Idempotency**: dedupe by `X-GitHub-Delivery` if needed.
- **Minimal processing**: enqueue work quickly, avoid long-running logic in request thread.
- **Logging**: log event type, delivery id, and extracted issue/PR identifiers.

## Implementation Notes

Listener implementation:

- Framework: FastAPI (`src/ace/webhooks/app.py`)
- Endpoint: `/github/webhooks`
- Deployment target: Cloud Run
- Orchestration: direct call into the agent pool (remote)
