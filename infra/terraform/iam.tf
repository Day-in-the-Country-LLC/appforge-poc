resource "google_service_account" "ace_sa" {
  account_id   = "appforge-coding-engine"
  display_name = "Appforge Coding Engine Service Account"
}

resource "google_project_iam_member" "ace_secret_accessor" {
  project = var.gcp_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.ace_sa.email}"
}

resource "google_project_iam_member" "ace_cloud_run_invoker" {
  project = var.gcp_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.ace_sa.email}"
}

output "service_account_email" {
  description = "Service account email"
  value       = google_service_account.ace_sa.email
}
