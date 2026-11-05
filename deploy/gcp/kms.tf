# Customer-managed encryption keys (CMEK) for Artifact Registry and the eval-report bucket.
resource "google_kms_key_ring" "jobscout" {
  name       = "jobscout"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "artifacts" {
  name            = "artifact-registry"
  key_ring        = google_kms_key_ring.jobscout.id
  rotation_period = "7776000s" # 90 days
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key" "eval_bucket" {
  name            = "eval-reports"
  key_ring        = google_kms_key_ring.jobscout.id
  rotation_period = "7776000s"
  lifecycle {
    prevent_destroy = true
  }
}

# Service agents must be allowed to use the keys.
# Artifact Registry service agent (google_project_service_identity is google-beta only).
resource "google_kms_crypto_key_iam_member" "ar_uses_key" {
  crypto_key_id = google_kms_crypto_key.artifacts.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-artifactregistry.iam.gserviceaccount.com"
}

data "google_storage_project_service_account" "gcs" {}

resource "google_kms_crypto_key_iam_member" "gcs_uses_key" {
  crypto_key_id = google_kms_crypto_key.eval_bucket.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}
