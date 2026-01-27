# Twilio SMS Notifications

The Appforge Coding Engine can send SMS notifications via Twilio when PRs are created, so you're immediately notified of completed work.

## Setup

### 1. Get Twilio Credentials

1. Go to [Twilio Console](https://www.twilio.com/console)
2. Find your **Account SID** and **Auth Token**
3. Get or create a **Twilio phone number** (from Phone Numbers section)
4. Have your **personal phone number** ready

### 2. Configure Environment Variables

Add to your `.env` file:

```env
TWILIO_ENABLED=true
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_MESSAGING_SERVICE_SID=MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_TO_NUMBER=+1234567890
```

**Note:** Using a Messaging Service SID is the recommended production approach. It allows Twilio to manage phone numbers and routing automatically.

### 3. Store in GCP Secret Manager

When deploying to GCP, the bootstrap script will prompt for these values:

```bash
./infra/scripts/bootstrap_gcp.sh your-gcp-project-id
```

## How It Works

When an agent completes work and opens a PR:

1. **Engine detects PR creation**
2. **Twilio client formats message** with:
   - Issue number and title
   - Repository name
   - PR number and link
   - Brief summary of work
3. **SMS sent to your phone** with review link
4. **You review and merge** at your convenience

### Example SMS

```
🚀 PR Ready for Review

Issue #42: Add dark mode support
Repo: frontend-repo
PR: #789

Summary:
Implemented dark mode toggle in settings menu. All components support dark mode styling. User preference persists in local storage.

Review: https://github.com/org/repo/pull/789
```

## Disabling Notifications

To disable SMS notifications without removing configuration:

```env
TWILIO_ENABLED=false
```

Or set in GCP:

```bash
gcloud secrets versions add twilio-enabled --data-file=- <<< "false"
```

## Troubleshooting

### SMS not being sent

1. **Verify TWILIO_ENABLED=true** in environment
2. **Check Twilio credentials** are correct
3. **Verify phone numbers** are in E.164 format (+1234567890)
4. **Check Twilio account balance** (trial accounts have limits)
5. **Review logs** for error messages:

```bash
# Local
grep "sms_send_failed" /tmp/agent-hq/logs/*.log

# GCP
gcloud run services logs read appforge-coding-engine --region us-central1 | grep sms
```

### Wrong phone number receiving messages

- Verify `TWILIO_TO_NUMBER` is correct
- Check `TWILIO_FROM_NUMBER` is a valid Twilio number
- Ensure numbers are in E.164 format

### Rate limiting

Twilio has rate limits. If you're testing heavily:
- Use trial account limits appropriately
- Consider upgrading Twilio account for production
- Space out PR creation for testing

## Cost

Twilio pricing varies by region. In the US:
- Outbound SMS: ~$0.0075 per message
- Inbound SMS: ~$0.0075 per message

For a typical workflow (1-5 PRs per day), costs are minimal.

## Disabling for Development

For local development without sending real SMS:

```env
TWILIO_ENABLED=false
```

Notifications will be logged but not sent. Check logs to verify formatting.

## Advanced: Custom Message Format

To customize notification messages, edit `src/ace/notifications/twilio_client.py`:

- `_format_pr_message()` - PR notification format
- `_format_blocked_message()` - Blocked notification format

## Feature: Blocked Notifications (Future)

The engine also supports SMS notifications when agents get blocked:

```
⏸️ Agent Blocked

Issue #42: Add dark mode support

Question:
Should dark mode be opt-in or default?

Reply in GitHub to resume.
```

This is implemented but not yet integrated into the workflow. Enable by calling `notifier.send_blocked_notification()` in the `handle_blocked` node.
