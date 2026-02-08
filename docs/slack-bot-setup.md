# Slack Bot Setup (Notifications)

This guide walks through creating a Slack App (bot) and gathering the values needed for posting messages from the webhook listener.

## What You Need

- Slack workspace admin access (or permission to create apps)
- A channel for notifications

## Create the Slack App

1. Open a browser and go to the Slack API site:
   - From any Slack page, click your workspace name (top left) -> **Tools & settings** -> **Manage apps**.
   - In the App Directory page, click **Build** (top right) to open the Slack API site.
   - Or go directly to `https://api.slack.com/apps`.
2. Click **Create New App**.
3. Choose **From scratch**.
4. Name the app (example: `Appforge Listener`) and select your workspace.
5. Click **Create App**.

## Configure Bot Token Scopes

1. In the left sidebar, go to **OAuth & Permissions**.
2. Under **Bot Token Scopes**, add:
   - `chat:write` (required to post messages)
   - `channels:read` (required if you want to look up channel IDs)
   - `groups:read` (required if you post to private channels)
3. Save changes.

## Install the App to the Workspace

1. Still in **OAuth & Permissions**, click **Install to Workspace**.
2. Approve the permissions.
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`).

This token is the value to store as `SLACK_BOT_TOKEN`.

## Get the Channel ID

Slack API uses channel IDs (not names) for posting messages.

Option A (Slack UI):
1. Open the target channel in Slack.
2. Click the channel name at the top.
3. Look for the **Channel ID** in the channel details.

Option B (Slack API):
1. Use the bot token and call `conversations.list` to find the channel ID.
2. Use the channel ID from the response.

Example (replace with your token):

```bash
curl -sS -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  "https://slack.com/api/conversations.list"
```

## Invite the Bot to the Channel

In the target Slack channel, run:

```
/invite @YourBotName
```

Without this, `chat.postMessage` will fail even if the token is valid.

## Configure Secrets for the Listener

Store these as secrets and inject them into the listener service:

- `SLACK_BOT_TOKEN`: the `xoxb-` bot token
- `SLACK_CHANNEL_ID`: the channel ID (example: `C1234567890`)

Note: the listener also accepts `SLACKBOT_TOKEN` as an alternate environment variable name.

## Quick Test (Optional)

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data '{"channel":"'$SLACK_CHANNEL_ID'","text":"Listener test message"}' \
  https://slack.com/api/chat.postMessage
```

If successful, Slack returns JSON with `"ok": true`.
