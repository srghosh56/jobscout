resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "jobscout"
  format        = "DOCKER"
  description   = "JobScout and llm-gateway images"
  kms_key_name  = google_kms_crypto_key.artifacts.id

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.ar_uses_key]
}

resource "google_storage_bucket" "eval_reports" {
  name                        = "${var.project_id}-jobscout-eval-reports"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = google_kms_crypto_key.eval_bucket.id
  }

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 180
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_uses_key]
}
