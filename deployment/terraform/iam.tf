# Rubric #18 (Infrastructure as Code): Least-privilege IAM service accounts for runtime and CI/CD deployer.

locals {
  runtime_roles = toset([
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/dlp.user",
    "roles/modelarmor.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/telemetry.writer",
  ])

  deployer_roles = toset([
    "roles/run.admin",
    "roles/aiplatform.user",
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.editor",
    "roles/iam.serviceAccountUser",
    "roles/storage.admin",
  ])
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "alz-agent-runtime"
  display_name = "Alzheimer Recent-Research Agent Runtime SA"
  description  = "Least-privilege service account used by Cloud Run and Vertex AI Agent Engine at runtime."
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = local.runtime_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "alz-agent-deployer"
  display_name = "Alzheimer Recent-Research Agent CI/CD Deployer SA"
  description  = "Service account impersonated via GitHub Actions Workload Identity Federation for deployments."
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = local.deployer_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}
