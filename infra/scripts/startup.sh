#!/bin/bash
set -e

# Install dependencies
apt-get update
apt-get install -y curl ca-certificates git

# Create app directory
mkdir -p /opt/ace
cd /opt/ace

# Clone or update repo
REPO_URL="${REPO_URL:-https://github.com/your-org/appforge-poc.git}"

if [ -d "/opt/ace/appforge-poc" ]; then
    cd /opt/ace/appforge-poc
    git remote set-url origin "$REPO_URL"
    git pull origin main
else
    git clone "$REPO_URL" appforge-poc
    cd /opt/ace/appforge-poc
fi

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.cargo/bin:$PATH"

# Install Python and create virtual environment
uv python install 3.12
uv venv /opt/ace/venv --python 3.12
source /opt/ace/venv/bin/activate

# Install dependencies
uv sync --frozen --no-dev --active

# NOTE: FastAPI/HTTP service removed. Configure your own scheduler/cron to run:
# UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 0
