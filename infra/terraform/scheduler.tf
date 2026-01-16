# Daily morning trigger for agent runs
resource "google_cloud_scheduler_job" "ace_morning_run" {
  name             = "ace-morning-run"
  description      = "Daily morning trigger - process all unblocked issues"
  schedule         = "0 8 * * *"  # 8:00 AM daily
  time_zone        = "America/New_York"
  attempt_deadline = "1800s"  # 30 min timeout for the trigger
  region           = var.gcp_region

  http_target {
    http_method = "POST"
    uri         = "http://${google_compute_instance.ace_vm.network_interface[0].access_config[0].nat_ip}:8080/agents/run"
  }

  depends_on = [
    google_compute_instance.ace_vm,
  ]
}

output "scheduler_job" {
  description = "Cloud Scheduler job name"
  value       = google_cloud_scheduler_job.ace_morning_run.name
}
