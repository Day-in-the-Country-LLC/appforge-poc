#!/bin/bash
# Deploy ACE to GCP Compute Engine VM
# Usage: ./deploy_vm.sh --project-id <id> --service-account <email> --repo-url <url> [--zone <zone>]

set -e

# Required args (fail fast)
PROJECT_ID=""
SA_EMAIL=""
REPO_URL=""
ZONE="us-central1-a"
VM_NAME="ace-vm"
MACHINE_TYPE="e2-medium"
DISK_SIZE="30"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-id)
            PROJECT_ID="$2"
            shift 2
            ;;
        --service-account)
            SA_EMAIL="$2"
            shift 2
            ;;
        --repo-url)
            REPO_URL="$2"
            shift 2
            ;;
        --zone)
            ZONE="$2"
            shift 2
            ;;
        --vm-name)
            VM_NAME="$2"
            shift 2
            ;;
        --machine-type)
            MACHINE_TYPE="$2"
            shift 2
            ;;
        --disk-size)
            DISK_SIZE="$2"
            shift 2
            ;;
        *)
            echo "❌ ERROR: Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [ -z "$PROJECT_ID" ]; then
    echo "❌ ERROR: Missing --project-id"
    exit 1
fi
if [ -z "$SA_EMAIL" ]; then
    echo "❌ ERROR: Missing --service-account"
    exit 1
fi
if [ -z "$REPO_URL" ]; then
    echo "❌ ERROR: Missing --repo-url"
    exit 1
fi

echo "=== ACE VM Deployment ==="
echo "Project: $PROJECT_ID"
echo "Service Account: $SA_EMAIL"
echo "Zone: $ZONE"
echo "VM: $VM_NAME ($MACHINE_TYPE)"
echo ""

# Set project
gcloud config set project "$PROJECT_ID"

# Enable Compute API (Secret Manager already enabled)
echo "Enabling APIs..."
gcloud services enable compute.googleapis.com
gcloud services enable cloudscheduler.googleapis.com

# Verify secrets exist in Secret Manager
echo ""
echo "Verifying secrets in Secret Manager..."
missing_secrets=0
for SECRET in github-control-api-key APPFORGE_OPENAI_API_KEY CLAUDE_CODE_ADMIN_API_KEY; do
    if gcloud secrets describe "$SECRET" &>/dev/null 2>&1; then
        echo "✓ Secret exists: $SECRET"
    else
        echo "❌ ERROR: Missing secret: $SECRET"
        echo "  Add with: gcloud secrets create $SECRET --data-file=<file>"
        missing_secrets=1
    fi
done
if [ "$missing_secrets" -ne 0 ]; then
    echo "❌ ERROR: Required secrets are missing. Aborting."
    exit 1
fi

# Create VPC network if it doesn't exist
if ! gcloud compute networks describe ace-network &>/dev/null 2>&1; then
    echo "Creating VPC network..."
    gcloud compute networks create ace-network --subnet-mode=auto
fi

# Create firewall rule if it doesn't exist
if ! gcloud compute firewall-rules describe ace-allow-http &>/dev/null 2>&1; then
    echo "Creating firewall rule..."
    gcloud compute firewall-rules create ace-allow-http \
        --network=ace-network \
        --allow=tcp:8080,tcp:22 \
        --target-tags=ace \
        --description="Allow HTTP and SSH traffic to ACE service"
fi

# Create startup script
STARTUP_SCRIPT=$(cat << 'EOF'
#!/bin/bash
set -e

# Repo URL (injected by deploy_vm.sh)
REPO_URL="__REPO_URL__"

# Install dependencies
apt-get update
apt-get install -y curl ca-certificates git

# Create app directory
mkdir -p /opt/ace
cd /opt/ace

# Clone or update repo
if [ -d "/opt/ace/appforge-poc" ]; then
    cd /opt/ace/appforge-poc
    git remote set-url origin "${REPO_URL}"
    git pull origin main
else
    git clone "${REPO_URL}" appforge-poc
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

# Get secrets from Secret Manager (using correct secret names)
export GITHUB_TOKEN=$(gcloud secrets versions access latest --secret=github-control-api-key 2>/dev/null || echo "")
export APPFORGE_OPENAI_API_KEY=$(gcloud secrets versions access latest --secret=APPFORGE_OPENAI_API_KEY 2>/dev/null || echo "")
export CLAUDE_CODE_ADMIN_API_KEY=$(gcloud secrets versions access latest --secret=CLAUDE_CODE_ADMIN_API_KEY 2>/dev/null || echo "")

# NOTE: FastAPI/HTTP service removed. Configure your own scheduler/cron to run:
# UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/run_agent_pool.py --target remote --max-issues 0
EOF
)
STARTUP_SCRIPT=${STARTUP_SCRIPT/__REPO_URL__/${REPO_URL}}

# Check if VM exists
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" &>/dev/null 2>&1; then
    echo ""
    echo "VM '$VM_NAME' already exists."
    read -p "Delete and recreate? (y/N): " RECREATE
    if [ "$RECREATE" = "y" ] || [ "$RECREATE" = "Y" ]; then
        echo "Deleting existing VM..."
        gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --quiet
    else
        echo "Updating startup script on existing VM..."
        gcloud compute instances add-metadata "$VM_NAME" \
            --zone="$ZONE" \
            --metadata=startup-script="$STARTUP_SCRIPT"
        
        echo "Restarting VM to apply changes..."
        gcloud compute instances reset "$VM_NAME" --zone="$ZONE"
        
        EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
        echo ""
        echo "=== Deployment Complete ==="
        echo "VM: $VM_NAME"
        echo "External IP: $EXTERNAL_IP"
        echo "Service URL: http://$EXTERNAL_IP:8080"
        echo ""
        echo "Check status: curl http://$EXTERNAL_IP:8080/health"
        exit 0
    fi
fi

# Create VM
echo "Creating VM..."
gcloud compute instances create "$VM_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --boot-disk-size="${DISK_SIZE}GB" \
    --boot-disk-type=pd-standard \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --network=ace-network \
    --service-account="$SA_EMAIL" \
    --scopes=cloud-platform \
    --tags=ace \
    --metadata=startup-script="$STARTUP_SCRIPT" \
    --metadata=enable-oslogin=TRUE

# Get external IP
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "=== Deployment Complete ==="
echo "VM: $VM_NAME"
echo "Zone: $ZONE"
echo "External IP: $EXTERNAL_IP"
echo "Service URL: http://$EXTERNAL_IP:8080"
echo ""
echo "The VM is starting up. Wait 2-3 minutes for initialization."
echo ""
echo "Commands:"
echo "  Check health:  curl http://$EXTERNAL_IP:8080/health"
echo "  View logs:     gcloud compute ssh $VM_NAME --zone=$ZONE --command='sudo journalctl -u ace -f'"
echo "  Trigger run:   curl -X POST http://$EXTERNAL_IP:8080/agents/run"
echo "  SSH:           gcloud compute ssh $VM_NAME --zone=$ZONE"
echo ""

# Create Cloud Scheduler job for daily runs
echo "Creating Cloud Scheduler job..."
REGION="${ZONE%-*}"  # Extract region from zone

# Check if scheduler job exists
if gcloud scheduler jobs describe ace-morning-run --location="$REGION" &>/dev/null 2>&1; then
    echo "Scheduler job already exists, updating..."
    gcloud scheduler jobs update http ace-morning-run \
        --location="$REGION" \
        --schedule="0 8 * * *" \
        --time-zone="America/New_York" \
        --uri="http://$EXTERNAL_IP:8080/agents/run" \
        --http-method=POST \
        --quiet
else
    gcloud scheduler jobs create http ace-morning-run \
        --location="$REGION" \
        --schedule="0 8 * * *" \
        --time-zone="America/New_York" \
        --uri="http://$EXTERNAL_IP:8080/agents/run" \
        --http-method=POST \
        --description="Daily morning trigger for ACE agent runs"
fi

echo ""
echo "Scheduler configured: Daily at 8:00 AM Eastern"
echo "Manual trigger: gcloud scheduler jobs run ace-morning-run --location=$REGION"
