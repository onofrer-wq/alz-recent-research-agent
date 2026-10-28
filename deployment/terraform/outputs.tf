# Rubric #18 (Infrastructure as Code): Terraform outputs for CI/CD workflows and verification.

output "cloud_run_url" {
  description = "HTTPS endpoint URL of the deployed Cloud Run v2 agent service."
  value       = google_cloud_run_v2_service.agent.uri
}

output "runtime_service_account_email" {
  description = "Email of the least-privilege runtime service account (alz-agent-runtime)."
  value       = google_service_account.runtime.email
}

output "deployer_service_account_email" {
  description = "Email of the CI/CD deployer service account (alz-agent-deployer)."
  value       = google_service_account.deployer.email
}

output "wif_provider_name" {
  description = "Full resource name of the GitHub Actions Workload Identity Federation OIDC provider."
  value       = google_iam_workload_identity_pool_provider.github_oidc.name
}

output "model_armor_prompt_template" {
  description = "Full resource name of the Model Armor prompt screening template."
  value       = google_model_armor_template.prompt_template.name
}

output "model_armor_response_template" {
  description = "Full resource name of the Model Armor response screening template."
  value       = google_model_armor_template.response_template.name
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository URI for agent container images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agent.repository_id}"
}
