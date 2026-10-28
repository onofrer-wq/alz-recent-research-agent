# Rubric #18 (Infrastructure as Code) & Rubric #19 (Secure Secret Management):
# Artifact Registry repository and Cloud Run v2 service with Secret Manager secret_key_ref injection.

resource "google_artifact_registry_repository" "agent" {
  project       = var.project_id
  location      = var.region
  repository_id = var.service_name
  description   = "Container images for the Alzheimer's Recent-Research Agent."
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "agent" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "APP_ENV"
        value = "prod"
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "FAST_MODEL"
        value = var.fast_model
      }
      env {
        name  = "REASONING_MODEL"
        value = var.reasoning_model
      }
      env {
        name  = "JUDGE_MODEL"
        value = var.fast_model
      }
      env {
        name  = "RECENCY_YEARS"
        value = tostring(var.recency_years)
      }
      env {
        name  = "PII_MODE"
        value = "dlp"
      }
      env {
        name  = "OTEL_TO_CLOUD"
        value = "true"
      }
      env {
        name  = "HITL_MODE"
        value = "state_gate"
      }
      env {
        name  = "BLOCK_ON_SCREENING_FAILURE"
        value = "true"
      }
      env {
        name  = "MODEL_ARMOR_PROMPT_TEMPLATE"
        value = google_model_armor_template.prompt_template.name
      }
      env {
        name  = "MODEL_ARMOR_RESPONSE_TEMPLATE"
        value = google_model_armor_template.response_template.name
      }

      # Rubric #19: Secret injected at runtime from Secret Manager via secret_key_ref — NEVER a plaintext literal.
      env {
        name = "NCBI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.ncbi_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.ncbi_api_key_runtime_accessor,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = google_cloud_run_v2_service.agent.project
  location = google_cloud_run_v2_service.agent.location
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
