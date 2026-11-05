# Direct VPC egress: Cloud Run sends traffic to private ranges through this subnet, so
# jobscout reaches the internal llm-gateway without leaving Google's network.
resource "google_compute_network" "vpc" {
  name                    = "jobscout-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "run" {
  name                     = "jobscout-run-${var.region}"
  ip_cidr_range            = "10.10.0.0/24"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}
