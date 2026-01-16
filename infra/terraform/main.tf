terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

variable "gcp_project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "gcp_region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "agentic-coding-engine"
}

variable "image_url" {
  description = "Container image URL"
  type        = string
}

variable "environment_variables" {
  description = "Environment variables for the service"
  type        = map(string)
  default     = {}
}

resource "google_cloud_run_service" "ace_service" {
  name     = var.service_name
  location = var.gcp_region

  template {
    spec {
      service_account_email = google_service_account.ace_sa.email

      containers {
        image = var.image_url

        env {
          name  = "ENVIRONMENT"
          value = "production"
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.gcp_project_id
        }

        env {
          name  = "GCP_SECRET_MANAGER_ENABLED"
          value = "true"
        }

        dynamic "env" {
          for_each = var.environment_variables
          content {
            name  = env.key
            value = env.value
          }
        }

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }
      }

      timeout_seconds = 3600
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  depends_on = [
    google_project_iam_member.ace_secret_accessor,
  ]
}

resource "google_cloud_run_service_iam_member" "ace_public" {
  service  = google_cloud_run_service.ace_service.name
  location = google_cloud_run_service.ace_service.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_service.ace_service.status[0].url
}
