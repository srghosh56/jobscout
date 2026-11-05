output "jobscout_url" {
  value = google_cloud_run_v2_service.jobscout.uri
}

output "llm_gateway_url" {
  value = google_cloud_run_v2_service.llm_gateway.uri
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "wif_provider" {
  description = "Use as workload_identity_provider in google-github-actions/auth"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "eval_reports_bucket" {
  value = google_storage_bucket.eval_reports.url
}
