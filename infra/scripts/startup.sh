#!/bin/bash
set -e

# Install dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv git

# Get GitHub token from Secret Manager for repo access
GITHUB_TOKEN=$(gcloud secrets versions access latest --secret=GITHUB_CONTROL_API_KEY 2>/dev/null || echo "")

if [ -z "$GITHUB_TOKEN" ]; then
    echo "ERROR: Could not retrieve GitHub token from Secret Manager"
    exit 1
fi

# Create app directory
mkdir -p /opt/ace
cd /opt/ace

# Clone or update repo (using token for private repo access)
REPO_URL="https://${GITHUB_TOKEN}@github.com/Day-in-the-Country-LLC/appforge-poc.git"

if [ -d "/opt/ace/appforge-poc" ]; then
    cd /opt/ace/appforge-poc
    git remote set-url origin "$REPO_URL"
    git pull origin main
else
    git clone "$REPO_URL" appforge-poc
    cd /opt/ace/appforge-poc
fi

# Create virtual environment
python3 -m venv /opt/ace/venv
source /opt/ace/venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -e .

# NOTE: FastAPI/HTTP service removed. Configure your own scheduler/cron to run:
# UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 0
