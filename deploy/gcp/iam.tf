# One least-privilege service account per service, plus a deployer for GitHub Actions.
resource "google_service_account" "jobscout" {
  account_id   = "jobscout-run"
  display_name = "JobScout Cloud Run runtime"
}

resource "google_service_account" "llm_gateway" {
  account_id   = "llm-gateway-run"
  display_name = "llm-gateway Cloud Run runtime"
}

resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deployer (WIF)"
}

# jobscout reads only its own secrets.
resource "google_secret_manager_secret_iam_member" "jobscout_secrets" {
  for_each  = toset(local.jobscout_secrets)
  secret_id = google_secret_manager_secret.s[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.jobscout.member
}

# llm-gateway reads only the model API keys.
resource "google_secret_manager_secret_iam_member" "gateway_secrets" {
  for_each  = toset(local.llm_gateway_secrets)
  secret_id = google_secret_manager_secret.s[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.llm_gateway.member
}

# Only jobscout may call the internal gateway.
resource "google_cloud_run_v2_service_iam_member" "jobscout_invokes_gateway" {
  name     = google_cloud_run_v2_service.llm_gateway.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.jobscout.member
}

# Public web app: anyone may invoke jobscout (it has its own API keys + rate limits).
resource "google_cloud_run_v2_service_iam_member" "public_jobscout" {
  name     = google_cloud_run_v2_service.jobscout.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Deployer: push images to this repository and roll out the two services only.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

resource "google_cloud_run_v2_service_iam_member" "deployer_run" {
  for_each = {
    jobscout    = google_cloud_run_v2_service.jobscout.name
    llm_gateway = google_cloud_run_v2_service.llm_gateway.name
  }
  name     = each.value
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  for_each = {
    jobscout    = google_service_account.jobscout.name
    llm_gateway = google_service_account.llm_gateway.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

# CI uploads eval reports (write only; no read/delete).
resource "google_storage_bucket_iam_member" "deployer_reports" {
  bucket = google_storage_bucket.eval_reports.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.deployer.member
}
