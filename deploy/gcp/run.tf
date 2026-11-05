locals {
  secret_env = {
    DATABASE_URL          = "database-url"
    READONLY_DATABASE_URL = "readonly-database-url"
    GEMINI_API_KEY        = "gemini-api-key"
    MISTRAL_API_KEY       = "mistral-api-key"
  }
}

resource "google_cloud_run_v2_service" "llm_gateway" {
  name                = "llm-gateway"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

  template {
    service_account = google_service_account.llm_gateway.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.run.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
    containers {
      image = var.llm_gateway_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      dynamic "env" {
        for_each = { GEMINI_API_KEY = "gemini-api-key", MISTRAL_API_KEY = "mistral-api-key" }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_billing_budget.monthly, google_secret_manager_secret_iam_member.gateway_secrets]
}

resource "google_cloud_run_v2_service" "jobscout" {
  name                = "jobscout"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.jobscout.email
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.run.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
    containers {
      image = var.jobscout_image
      ports {
        container_port = 8088
      }
      resources {
        limits = { cpu = "2", memory = "4Gi" }
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 10
        period_seconds        = 10
        failure_threshold     = 12
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
      env {
        name  = "LLM_GATEWAY_URL"
        value = google_cloud_run_v2_service.llm_gateway.uri
      }
      dynamic "env" {
        for_each = var.jobscout_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_billing_budget.monthly, google_secret_manager_secret_iam_member.jobscout_secrets]
}
