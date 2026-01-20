# GCP Deployment Guide

This guide walks through deploying the Agentic Coding Engine to Google Cloud Platform using a persistent VM (non-HTTP; runs a drain and exits).

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

## Step 2: Deploy with Terraform (VM optional; HTTP service removed)

```bash
cd infra/terraform

PROJECT_ID=your-gcp-project-id
REGION=us-central1

terraform init

terraform plan -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION

terraform apply -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION
```

Terraform can create a VM skeleton, but the previous HTTP service has been removed. For daily runs, trigger the CLI (e.g., via cron/Cloud Scheduler invoking the CLI) rather than an HTTP endpoint.

## Step 3: Verify Deployment

Get the VM's external IP:

```bash
gcloud compute instances describe ace-vm --zone us-central1-a --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
```

Previous HTTP endpoints are removed (no FastAPI service). Use CLI runs instead.

## Triggering daily runs

Use a cron/Cloud Scheduler job to execute the CLI on the VM:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 0
```

## Step 5: Create GitHub Labels

In your repository, create the following labels:

- `agent:ready` - Issue ready for agent pickup
- `agent:in-progress` - Agent is working on it
- `agent:blocked` - Agent is blocked, waiting for input
- `agent:done` - Agent completed work
- `agent:failed` - Agent encountered error

## Monitoring

```bash
gcloud secrets list
gcloud secrets versions list github-token
```

## Troubleshooting

No HTTP service is running. Monitor the CLI job logs (cron/Cloud Scheduler/ssh).

## Cleanup

To destroy all resources:

```bash
terraform destroy -var gcp_project_id=$PROJECT_ID -var gcp_region=$REGION
```

Then manually delete secrets if desired.

### Local Agent Setup

For issues requiring local machine access (e.g., Redis migration), run the CLI drain locally:

```bash
cd appforge-poc
source .venv/bin/activate
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target local --max-issues 0
```
