# GCE VM for Agentic Coding Engine
# e2-medium with 30GB persistent disk

variable "vm_zone" {
  description = "GCP Zone for the VM"
  type        = string
  default     = "us-central1-a"
}

variable "vm_name" {
  description = "VM instance name"
  type        = string
  default     = "ace-vm"
}

resource "google_compute_instance" "ace_vm" {
  name         = var.vm_name
  machine_type = "e2-medium"
  zone         = var.vm_zone

  tags = ["ace", "http-server", "https-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
      type  = "pd-standard"
    }
  }

  network_interface {
    network = "default"
    access_config {
      # Ephemeral public IP
    }
  }

  service_account {
    email  = google_service_account.ace_sa.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = <<-EOF
    #!/bin/bash
    set -e

    # Install dependencies
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git docker.io

    # Enable Docker
    systemctl enable docker
    systemctl start docker

    # Create app directory
    mkdir -p /opt/ace
    cd /opt/ace

    # Clone the repo (or pull if exists)
    if [ -d "/opt/ace/appforge-poc" ]; then
      cd /opt/ace/appforge-poc && git pull
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
    Environment="GCP_PROJECT_ID=${var.gcp_project_id}"
    Environment="GCP_SECRET_MANAGER_ENABLED=true"
    ExecStart=/opt/ace/venv/bin/uvicorn ace.runners.service:app --host 0.0.0.0 --port 8080
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
    SERVICEEOF

    # Enable and start service
    systemctl daemon-reload
    systemctl enable ace
    systemctl start ace

    echo "ACE service started"
  EOF

  labels = {
    app = "agentic-coding-engine"
    env = "production"
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  allow_stopping_for_update = true
}

# Firewall rule for HTTP access
resource "google_compute_firewall" "ace_http" {
  name    = "ace-allow-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["ace"]
}

output "vm_external_ip" {
  description = "External IP of the ACE VM"
  value       = google_compute_instance.ace_vm.network_interface[0].access_config[0].nat_ip
}

output "vm_internal_ip" {
  description = "Internal IP of the ACE VM"
  value       = google_compute_instance.ace_vm.network_interface[0].network_ip
}

output "service_endpoint" {
  description = "ACE service endpoint"
  value       = "http://${google_compute_instance.ace_vm.network_interface[0].access_config[0].nat_ip}:8080"
}
