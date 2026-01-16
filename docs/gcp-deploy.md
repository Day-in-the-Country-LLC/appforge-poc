# GCP Deployment Guide

This guide walks through deploying the Agentic Coding Engine to Google Cloud Platform using a persistent VM.

## Architecture

- **e2-medium VM** (2 vCPU, 4 GB RAM, 30 GB disk)
- **Cloud Scheduler** triggers daily at 8 AM Eastern
- **Secret Manager** for API keys and tokens
- Runs until all unblocked issues are processed, then waits for next trigger

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI installed and authenticated
- `terraform` installed (v1.0+)
- GitHub PAT with `repo` and `read:org` scopes

## Step 1: Bootstrap GCP Project

Run the bootstrap script to set up APIs, service accounts, and secrets:

```bash
chmod +x infra/scripts/bootstrap_gcp.sh
./infra/scripts/bootstrap_gcp.sh your-gcp-project-id us-central1
```

This will:
- Enable required APIs (Compute Engine, Secret Manager, Cloud Scheduler)
- Create a service account
- Grant necessary IAM roles
- Prompt for secrets (GitHub token, API keys)

## Step 2: Deploy with Terraform

```bash
cd infra/terraform

PROJECT_ID=your-gcp-project-id
REGION=us-central1

terraform init

terraform plan -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION

terraform apply -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION
```

Terraform will create:
- e2-medium VM with startup script
- Cloud Scheduler job (daily 8 AM trigger)
- Firewall rules for HTTP access
- IAM bindings

## Step 3: Verify Deployment

Get the VM's external IP:

```bash
gcloud compute instances describe ace-vm --zone us-central1-a --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
```

Check service health:

```bash
curl http://<VM_IP>:8080/health
```

## Step 4: Configure GitHub Webhook (Optional)

If you want real-time webhook triggers in addition to scheduled runs:

1. Go to Settings → Webhooks → Add webhook
2. Payload URL: `http://<VM_IP>:8080/webhook/github`
3. Content type: `application/json`
4. Events: `Issues`, `Issue comments`
5. Secret: (use the value from bootstrap)

## Step 5: Create GitHub Labels

In your repository, create the following labels:

- `agent:ready` - Issue ready for agent pickup
- `agent:in-progress` - Agent is working on it
- `agent:blocked` - Agent is blocked, waiting for input
- `agent:done` - Agent completed work
- `agent:failed` - Agent encountered error

## Monitoring

### View VM Logs

```bash
# SSH into VM
gcloud compute ssh ace-vm --zone us-central1-a

# View service logs
sudo journalctl -u ace -f
```

### Check Agent Pool Status

```bash
curl http://<VM_IP>:8080/agents/status
```

### Check Scheduler Status

```bash
curl http://<VM_IP>:8080/scheduler/status

# Or via Cloud Scheduler
gcloud scheduler jobs describe ace-morning-run --location $REGION
```

### Manually Trigger a Run

```bash
curl -X POST http://<VM_IP>:8080/agents/run
```

### View Secret Manager

```bash
gcloud secrets list
gcloud secrets versions list github-token
```

## Troubleshooting

### Service won't start

SSH into the VM and check:
```bash
gcloud compute ssh ace-vm --zone us-central1-a
sudo systemctl status ace
sudo journalctl -u ace --no-pager -n 100
```

Common issues:
- Missing secrets in Secret Manager
- Invalid API keys
- Git clone failed (check network/permissions)

### Webhook not being received

1. Verify webhook URL uses VM's external IP
2. Check firewall allows port 8080
3. Check GitHub webhook delivery logs

### Scheduler not triggering

```bash
gcloud scheduler jobs run ace-morning-run --location $REGION
```

Check execution in Cloud Logging.

### Restart Service

```bash
gcloud compute ssh ace-vm --zone us-central1-a
sudo systemctl restart ace
```

## Cleanup

To destroy all resources:

```bash
terraform destroy -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION
```

Then manually delete secrets:
```bash
gcloud secrets delete github-token
gcloud secrets delete openai-api-key
gcloud secrets delete claude-api-key
```

## API Endpoints

All agent endpoints accept a `target` query parameter:
- `local` — Only process issues with `agent:local` label
- `remote` — Only process issues with `agent:remote` label (default for `/agents/run`)
- `any` — Process all issues regardless of label (default for other endpoints)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/agents/status?target=` | GET | Pool status (active/idle agents) |
| `/agents/run?target=` | POST | Process all unblocked issues until empty |
| `/agents/spawn?target=` | POST | Spawn agents for current ready issues |
| `/agents/stop?target=` | POST | Stop processing |
| `/scheduler/status` | GET | Next scheduled run time |
| `/scheduler/start` | POST | Start built-in daily scheduler |
| `/scheduler/stop` | POST | Stop built-in scheduler |

### Local Agent Setup

For issues requiring local machine access (e.g., Redis migration), run a local agent pool:

```bash
# In your local environment
cd appforge-poc
source .venv/bin/activate

# Start local agent pool (only processes agent:local issues)
curl -X POST http://localhost:8080/agents/run?target=local
```

Or run the service locally:
```bash
uvicorn ace.runners.service:app --port 8080
curl -X POST http://localhost:8080/agents/run?target=local
```
