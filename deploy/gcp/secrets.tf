# Secret containers only. Versions (the actual values) are added manually:
#   printf '%s' "$VALUE" | gcloud secrets versions add <name> --data-file=-
locals {
  jobscout_secrets    = ["database-url", "readonly-database-url", "gemini-api-key", "mistral-api-key"]
  llm_gateway_secrets = ["gemini-api-key", "mistral-api-key"]
}

resource "google_secret_manager_secret" "s" {
  for_each  = toset(local.jobscout_secrets)
  secret_id = "jobscout-${each.value}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
