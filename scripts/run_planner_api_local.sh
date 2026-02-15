#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project --quiet 2>/dev/null || true)}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "❌ ERROR: No GCP project id found. Set GCP_PROJECT_ID or run gcloud config set project." >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "❌ ERROR: gcloud CLI is required to load PLANNER_API_TOKEN from Secret Manager." >&2
  exit 1
fi

TOKEN_SECRET="${PLANNER_API_TOKEN_SECRET:-APPFORGE_PLANNER_API_TOKEN}"
TOKEN_VERSION="${PLANNER_SECRET_VERSION:-latest}"
PLANNER_TOKEN="$(gcloud secrets versions access "${TOKEN_VERSION}" --project "${PROJECT_ID}" --secret "${TOKEN_SECRET}")"

if [[ -z "${PLANNER_TOKEN}" ]]; then
  echo "❌ ERROR: Secret ${TOKEN_SECRET} is empty in project ${PROJECT_ID}." >&2
  exit 1
fi

echo "🚀 Starting planner API on ${PLANNER_TOKEN:0:4}… (project ${PROJECT_ID})"

exec env \
  GCP_PROJECT_ID="${PROJECT_ID}" \
  WEBHOOK_SERVICE_ROLE="${WEBHOOK_SERVICE_ROLE:-planner}" \
  PLANNER_API_TOKEN="${PLANNER_TOKEN}" \
  UVICORN_HOST="${UVICORN_HOST:-127.0.0.1}" \
  UVICORN_PORT="${UVICORN_PORT:-8000}" \
  uv run uvicorn ace.webhooks.app:app \
    --host "${UVICORN_HOST}" \
    --port "${UVICORN_PORT}" \
    --reload
