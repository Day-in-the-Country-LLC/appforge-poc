#!/bin/bash
set -e

PROJECT_ID=${1:-}
REGION=${2:-us-central1}

if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./bootstrap_gcp.sh <PROJECT_ID> [REGION]"
    exit 1
fi

echo "Bootstrapping GCP project: $PROJECT_ID in region: $REGION"

gcloud config set project "$PROJECT_ID"

echo "Enabling required APIs..."
gcloud services enable \
    run.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com

echo "Creating service account..."
gcloud iam service-accounts create agentic-coding-engine \
    --display-name="Agentic Coding Engine Service Account" \
    || echo "Service account already exists"

SA_EMAIL="agentic-coding-engine@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Granting IAM roles to service account..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/run.invoker"

echo "Creating secrets in Secret Manager..."
read -sp "GitHub Control API Key: " GITHUB_CONTROL_API_KEY
echo
gcloud secrets create github-control-api-key --data-file=- <<< "$GITHUB_CONTROL_API_KEY" 2>/dev/null || \
    gcloud secrets versions add github-control-api-key --data-file=- <<< "$GITHUB_CONTROL_API_KEY"

read -sp "GitHub Webhook Secret: " WEBHOOK_SECRET
echo
gcloud secrets create github-webhook-secret --data-file=- <<< "$WEBHOOK_SECRET" 2>/dev/null || \
    gcloud secrets versions add github-webhook-secret --data-file=- <<< "$WEBHOOK_SECRET"

read -sp "OpenAI API Key: " OPENAI_KEY
echo
gcloud secrets create openai-api-key --data-file=- <<< "$OPENAI_KEY" 2>/dev/null || \
    gcloud secrets versions add openai-api-key --data-file=- <<< "$OPENAI_KEY"

read -sp "Claude API Key: " CLAUDE_KEY
echo
gcloud secrets create claude-api-key --data-file=- <<< "$CLAUDE_KEY" 2>/dev/null || \
    gcloud secrets versions add claude-api-key --data-file=- <<< "$CLAUDE_KEY"

read -sp "Twilio Account SID: " TWILIO_ACCOUNT_SID
echo
gcloud secrets create twilio-account-sid --data-file=- <<< "$TWILIO_ACCOUNT_SID" 2>/dev/null || \
    gcloud secrets versions add twilio-account-sid --data-file=- <<< "$TWILIO_ACCOUNT_SID"

read -sp "Twilio Auth Token: " TWILIO_AUTH_TOKEN
echo
gcloud secrets create twilio-auth-token --data-file=- <<< "$TWILIO_AUTH_TOKEN" 2>/dev/null || \
    gcloud secrets versions add twilio-auth-token --data-file=- <<< "$TWILIO_AUTH_TOKEN"

read -sp "Twilio Messaging Service SID (e.g., MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx): " TWILIO_MESSAGING_SERVICE_SID
echo
gcloud secrets create twilio-messaging-service-sid --data-file=- <<< "$TWILIO_MESSAGING_SERVICE_SID" 2>/dev/null || \
    gcloud secrets versions add twilio-messaging-service-sid --data-file=- <<< "$TWILIO_MESSAGING_SERVICE_SID"

read -sp "Twilio To Number (your phone, e.g., +1234567890): " TWILIO_TO_NUMBER
echo
gcloud secrets create twilio-to-number --data-file=- <<< "$TWILIO_TO_NUMBER" 2>/dev/null || \
    gcloud secrets versions add twilio-to-number --data-file=- <<< "$TWILIO_TO_NUMBER"

echo "Bootstrap complete!"
echo "Service Account: $SA_EMAIL"
echo "Next steps:"
echo "1. Build and push Docker image to GCR"
echo "2. Deploy with Terraform: terraform apply -var gcp_project_id=$PROJECT_ID -var image_url=gcr.io/$PROJECT_ID/agentic-coding-engine:latest"
