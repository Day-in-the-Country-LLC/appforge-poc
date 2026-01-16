# GCP Deployment Guide

This guide walks through deploying the Agentic Coding Engine to Google Cloud Platform.

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI installed and authenticated
- `terraform` installed (v1.0+)
- Docker installed locally
- GitHub PAT with `repo` and `issues` scopes

## Step 1: Bootstrap GCP Project

Run the bootstrap script to set up APIs, service accounts, and secrets:

```bash
chmod +x infra/scripts/bootstrap_gcp.sh
./infra/scripts/bootstrap_gcp.sh your-gcp-project-id us-central1
```

This will:
- Enable required APIs (Cloud Run, Secret Manager, Cloud Scheduler)
- Create a service account
- Grant necessary IAM roles
- Prompt for secrets (GitHub token, webhook secret, API keys)

## Step 2: Build and Push Docker Image

```bash
PROJECT_ID=your-gcp-project-id
REGION=us-central1

docker build -t gcr.io/$PROJECT_ID/agentic-coding-engine:latest .

gcloud auth configure-docker
docker push gcr.io/$PROJECT_ID/agentic-coding-engine:latest
```

## Step 3: Deploy with Terraform

```bash
cd infra/terraform

terraform init

terraform plan \
  -var gcp_project_id=$PROJECT_ID \
  -var gcp_region=$REGION \
  -var image_url=gcr.io/$PROJECT_ID/agentic-coding-engine:latest

terraform apply \
  -var gcp_project_id=$PROJECT_ID \
  -var gcp_region=$REGION \
  -var image_url=gcr.io/$PROJECT_ID/agentic-coding-engine:latest
```

Terraform will create:
- Cloud Run service
- Cloud Scheduler polling job
- IAM bindings

## Step 4: Configure GitHub Webhook

After deployment, get the Cloud Run service URL:

```bash
gcloud run services describe agentic-coding-engine --region $REGION --format='value(status.url)'
```

In your GitHub repository settings:
1. Go to Settings → Webhooks → Add webhook
2. Payload URL: `https://your-cloud-run-url/webhook/github`
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

### View Logs

```bash
gcloud run services logs read agentic-coding-engine --region $REGION --limit 50
```

### Check Scheduler Executions

```bash
gcloud scheduler jobs describe agentic-coding-engine-polling --location $REGION
gcloud scheduler jobs run agentic-coding-engine-polling --location $REGION
```

### View Secret Manager

```bash
gcloud secrets list
gcloud secrets versions list github-token
```

## Troubleshooting

### Service won't start

Check logs:
```bash
gcloud run services logs read agentic-coding-engine --region $REGION --limit 100
```

Common issues:
- Missing secrets in Secret Manager
- Invalid API keys
- Insufficient IAM permissions

### Webhook not being received

1. Verify webhook URL is correct
2. Check GitHub webhook delivery logs (Settings → Webhooks → Recent Deliveries)
3. Verify webhook secret matches

### Scheduler not triggering

```bash
gcloud scheduler jobs run agentic-coding-engine-polling --location $REGION
```

Check execution logs in Cloud Logging.

## Cleanup

To destroy all resources:

```bash
terraform destroy \
  -var gcp_project_id=$PROJECT_ID \
  -var gcp_region=$REGION \
  -var image_url=gcr.io/$PROJECT_ID/agentic-coding-engine:latest
```

Then manually delete secrets:
```bash
gcloud secrets delete github-token
gcloud secrets delete github-webhook-secret
gcloud secrets delete openai-api-key
gcloud secrets delete claude-api-key
```

## Next Steps

- Set up CI/CD pipeline to automatically build and deploy on push
- Implement real agent backends (Codex, Claude)
- Add monitoring and alerting
- Set up log aggregation
