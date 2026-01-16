resource "google_secret_manager_secret" "github_token" {
  secret_id = "github-token"

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

output "secrets" {
  description = "Secret names for reference"
  value = {
    github_token           = google_secret_manager_secret.github_token.id
    github_webhook_secret  = google_secret_manager_secret.github_webhook_secret.id
    openai_api_key         = google_secret_manager_secret.openai_api_key.id
    claude_api_key         = google_secret_manager_secret.claude_api_key.id
  }
}
