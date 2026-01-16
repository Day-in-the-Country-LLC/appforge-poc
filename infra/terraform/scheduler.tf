resource "google_cloud_scheduler_job" "ace_polling" {
  name             = "agentic-coding-engine-polling"
  description      = "Polling trigger for agentic coding engine"
  schedule         = "*/5 * * * *"
  time_zone        = "UTC"
  attempt_deadline = "320s"
  region           = var.gcp_region

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_service.ace_service.status[0].url}/trigger/poll"

    oidc_token {
      service_account_email = google_service_account.ace_sa.email
    }
  }

  depends_on = [
    google_cloud_run_service.ace_service,
  ]
}

output "scheduler_job" {
  description = "Cloud Scheduler job name"
  value       = google_cloud_scheduler_job.ace_polling.name
}
