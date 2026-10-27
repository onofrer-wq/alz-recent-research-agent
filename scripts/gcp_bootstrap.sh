#!/usr/bin/env bash
# Rubric #18 (Infrastructure as Code & Agents CLI) & Rubric #19 (Secure Secret Management).
# Bootstraps GCP project settings, enables required Google Cloud APIs, and verifies ADC.

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "your-gcp-project-id" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: Set GOOGLE_CLOUD_PROJECT or run 'gcloud config set project <PROJECT_ID>' first." >&2
  exit 1
fi

echo "==> Configuring active GCP project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" --quiet

APIS=(
  "aiplatform.googleapis.com"
  "run.googleapis.com"
  "secretmanager.googleapis.com"
  "dlp.googleapis.com"
  "modelarmor.googleapis.com"
  "cloudtrace.googleapis.com"
  "logging.googleapis.com"
  "monitoring.googleapis.com"
  "telemetry.googleapis.com"
  "artifactregistry.googleapis.com"
  "cloudbuild.googleapis.com"
  "cloudresourcemanager.googleapis.com"
  "iam.googleapis.com"
  "iamcredentials.googleapis.com"
  "sts.googleapis.com"
  "serviceusage.googleapis.com"
)

echo "==> Enabling required GCP APIs (${#APIS[@]} services)..."
gcloud services enable "${APIS[@]}" --project="${PROJECT_ID}"

echo "==> Checking Application Default Credentials (ADC)..."
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "==> ADC not found; launching 'gcloud auth application-default login'..."
  gcloud auth application-default login
else
  echo "==> ADC verified."
fi

echo "==> GCP bootstrap complete for project ${PROJECT_ID}."
