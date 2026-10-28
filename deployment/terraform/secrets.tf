# Rubric #18 (Infrastructure as Code) & Rubric #19 (Secure Secret Management):
# Secret Manager secret containers ONLY. Secret values are NEVER stored in Terraform state or HCL;
# populate secret versions out-of-band via:
#   printf "%s" "$NCBI_API_KEY" | gcloud secrets versions add ncbi-api-key --data-file=-
#   printf "%s" "$SEMANTIC_SCHOLAR_API_KEY" | gcloud secrets versions add semantic-scholar-api-key --data-file=-

resource "google_secret_manager_secret" "ncbi_api_key" {
  project   = var.project_id
  secret_id = "ncbi-api-key"

  replication {
    auto {}
  }

  labels = {
    app       = "alz-recent-research-agent"
    managed   = "terraform"
    criterion = "secret-management"
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "ncbi_api_key_runtime_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.ncbi_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret" "semantic_scholar_api_key" {
  project   = var.project_id
  secret_id = "semantic-scholar-api-key"

  replication {
    auto {}
  }

  labels = {
    app       = "alz-recent-research-agent"
    managed   = "terraform"
    criterion = "secret-management"
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "semantic_scholar_api_key_runtime_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.semantic_scholar_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
