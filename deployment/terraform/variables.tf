# Rubric #18 (Infrastructure as Code): Input variables for the Alzheimer's Recent-Research Agent stack.

variable "project_id" {
  description = "Google Cloud project ID hosting Vertex AI, Cloud Run, DLP, Model Armor, and Secret Manager."
  type        = string
}

variable "region" {
  description = "Google Cloud region for Cloud Run, Artifact Registry, Vertex AI, and Model Armor templates."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Base name for the Cloud Run service and Artifact Registry repository."
  type        = string
  default     = "alz-recent-research-agent"
}

variable "github_repository" {
  description = "GitHub repository in 'owner/repo' format allowed to authenticate via Workload Identity Federation."
  type        = string
  default     = "onofrer-wq/alz-recent-research-agent"
}

variable "container_image" {
  description = "Container image URI deployed to Cloud Run v2."
  type        = string
  default     = "us-central1-docker.pkg.dev/ono-project-sandbox-55800/alz-recent-research-agent/agent:latest"
}

variable "fast_model" {
  description = "Gemini model ID for high-frequency routing, tool orchestration, and LLM-as-a-Judge checks."
  type        = string
  default     = "gemini-3-flash-preview"
}

variable "reasoning_model" {
  description = "Gemini model ID for deep multi-study synthesis and methodological critique."
  type        = string
  default     = "gemini-3.1-pro-preview-customtools"
}

variable "recency_years" {
  description = "Hard rolling publication-date floor in years enforced across biomedical search tools."
  type        = number
  default     = 3
}
