# Rubric #18 (Infrastructure as Code): Terraform versions and Google provider 6.x configuration.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Uncomment after running scripts/gcp_bootstrap.sh to store state in GCS:
  # backend "gcs" {
  #   bucket = "YOUR_PROJECT_ID-tfstate"
  #   prefix = "alz-research-agent/prod"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
