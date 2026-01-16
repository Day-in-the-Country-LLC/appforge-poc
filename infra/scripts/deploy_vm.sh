#!/bin/bash
# Deploy ACE to GCP Compute Engine VM
# Usage: ./deploy_vm.sh [ZONE]

set -e

# Use existing appforge project and service account
PROJECT_ID="appforge-483920"
SA_EMAIL="appforge@appforge-483920.iam.gserviceaccount.com"
ZONE="${1:-us-central1-a}"
VM_NAME="ace-vm"
MACHINE_TYPE="e2-medium"
DISK_SIZE="30"

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
for SECRET in GITHUB_CONTROL_API_KEY APPFORGE_OPENAI_API_KEY CLAUDE_CODE_ADMIN_API_KEY; do
    if gcloud secrets describe "$SECRET" &>/dev/null 2>&1; then
        echo "✓ Secret exists: $SECRET"
    else
        echo "✗ Missing secret: $SECRET"
        echo "  Add with: gcloud secrets create $SECRET --data-file=<file>"
    fi
done

# Create firewall rule if it doesn't exist
if ! gcloud compute firewall-rules describe ace-allow-http &>/dev/null 2>&1; then
    echo "Creating firewall rule..."
    gcloud compute firewall-rules create ace-allow-http \
        --allow=tcp:8080 \
        --target-tags=ace \
        --description="Allow HTTP traffic to ACE service"
fi

# Create startup script
STARTUP_SCRIPT=$(cat << 'EOF'
#!/bin/bash
set -e

# Install dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv git

# Create app directory
mkdir -p /opt/ace
cd /opt/ace

# Clone or update repo
if [ -d "/opt/ace/appforge-poc" ]; then
    cd /opt/ace/appforge-poc && git pull origin main
else
    git clone https://github.com/Day-in-the-Country-LLC/appforge-poc.git
    cd /opt/ace/appforge-poc
fi

# Create virtual environment
python3 -m venv /opt/ace/venv
source /opt/ace/venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -e .

# Get secrets from Secret Manager
export GITHUB_CONTROL_API_KEY=$(gcloud secrets versions access latest --secret=github-token 2>/dev/null || echo "")
export APPFORGE_OPENAI_API_KEY=$(gcloud secrets versions access latest --secret=openai-api-key 2>/dev/null || echo "")
export CLAUDE_CODE_ADMIN_API_KEY=$(gcloud secrets versions access latest --secret=claude-api-key 2>/dev/null || echo "")

# Create systemd service
cat > /etc/systemd/system/ace.service << 'SERVICEEOF'
[Unit]
Description=Agentic Coding Engine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/ace/appforge-poc
Environment="PATH=/opt/ace/venv/bin:/usr/bin"
Environment="ENVIRONMENT=production"
ExecStart=/bin/bash -c 'source /opt/ace/venv/bin/activate && \
    export GITHUB_CONTROL_API_KEY=$(gcloud secrets versions access latest --secret=github-token) && \
    export APPFORGE_OPENAI_API_KEY=$(gcloud secrets versions access latest --secret=openai-api-key 2>/dev/null || echo "") && \
    export CLAUDE_CODE_ADMIN_API_KEY=$(gcloud secrets versions access latest --secret=claude-api-key 2>/dev/null || echo "") && \
    uvicorn ace.runners.service:app --host 0.0.0.0 --port 8080'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Enable and start service
systemctl daemon-reload
systemctl enable ace
systemctl restart ace

echo "ACE service started"
EOF
)

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
