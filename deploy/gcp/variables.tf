variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, KMS and the bucket"
  type        = string
  default     = "europe-west3" # Frankfurt
}

variable "billing_account_id" {
  description = "Billing account (format 000000-000000-000000) for the budget"
  type        = string
}

variable "budget_amount_eur" {
  description = "Monthly budget; alerts at 50/90/100% of actual and 100% of forecast"
  type        = number
  default     = 20
}

variable "alert_email" {
  description = "E-mail for budget and monitoring alerts"
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to deploy via Workload Identity Federation"
  type        = string
}

variable "jobscout_image" {
  description = "Image for the jobscout service (Artifact Registry path:tag)"
  type        = string
}

variable "llm_gateway_image" {
  description = "Image for the internal llm-gateway service"
  type        = string
}

variable "jobscout_env" {
  description = "Non-secret environment variables for jobscout"
  type        = map(string)
  default = {
    LLM_PROVIDER     = "gemini"
    EMBED_PROVIDER   = "st"
    RERANKER_ENABLED = "true"
    OTEL_ENABLED     = "false"
    LOG_JSON         = "true"
  }
}
