resource "google_secret_manager_secret" "github_control_api_key" {
  secret_id = "github-control-api-key"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "github_webhook_secret" {
  secret_id = "github-webhook-secret"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "openai-api-key"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "claude_api_key" {
  secret_id = "claude-api-key"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "twilio_account_sid" {
  secret_id = "twilio-account-sid"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "twilio_auth_token" {
  secret_id = "twilio-auth-token"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "twilio_messaging_service_sid" {
  secret_id = "twilio-messaging-service-sid"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "twilio_to_number" {
  secret_id = "twilio-to-number"

  replication {
    automatic = true
  }
}

output "secrets" {
  description = "Secret names for reference"
  value = {
    github_control_api_key = google_secret_manager_secret.github_control_api_key.id
    github_webhook_secret  = google_secret_manager_secret.github_webhook_secret.id
    openai_api_key         = google_secret_manager_secret.openai_api_key.id
    claude_api_key         = google_secret_manager_secret.claude_api_key.id
    twilio_account_sid         = google_secret_manager_secret.twilio_account_sid.id
    twilio_auth_token          = google_secret_manager_secret.twilio_auth_token.id
    twilio_messaging_service_sid = google_secret_manager_secret.twilio_messaging_service_sid.id
    twilio_to_number           = google_secret_manager_secret.twilio_to_number.id
  }
}
