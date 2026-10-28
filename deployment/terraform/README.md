# Terraform Infrastructure as Code (`deployment/terraform/`)

Implements **Rubric #18 (Infrastructure as Code & Agents CLI)** and **Rubric #19 (Secure Secret Management)** for the **Alzheimer's Recent-Research Agent**.

## File Map

| File | Provisioned Resources |
|---|---|
| `versions.tf` | Terraform `>= 1.6.0`, `hashicorp/google ~> 6.0` provider, and optional GCS remote state backend. |
| `variables.tf` | Input variables (`project_id`, `region`, `service_name`, `github_repository`, `container_image`, `fast_model`, `reasoning_model`, `recency_years`). |
| `apis.tf` | `google_project_service` resources enabling Vertex AI, Cloud Run, Secret Manager, Cloud DLP, Model Armor, Cloud Trace, Logging, Monitoring, Telemetry, Artifact Registry, Cloud Build, and IAM (`disable_on_destroy = false`). |
| `iam.tf` | Least-privilege runtime service account (`alz-agent-runtime`) and CI/CD deployer service account (`alz-agent-deployer`) with scoped IAM roles. |
| `secrets.tf` | Secret Manager secret containers (`ncbi-api-key`, `semantic-scholar-api-key`) without plaintext secret values in state, plus `roles/secretmanager.secretAccessor` bindings for `alz-agent-runtime`. |
| `model_armor.tf` | Two `google_model_armor_template` resources (`prompt_template` and `response_template`) enforcing prompt injection/jailbreak, malicious URI, RAI (`DANGEROUS`, `HARASSMENT`, `HATE_SPEECH`, `SEXUALLY_EXPLICIT`), and SDP filters. |
| `runtime.tf` | Docker `google_artifact_registry_repository` and `google_cloud_run_v2_service` running as `alz-agent-runtime` with scale-to-zero (`0..5`), port `8080`, Model Armor template references, and `NCBI_API_KEY` injected via `value_source.secret_key_ref`. |
| `wif.tf` | Keyless GitHub Actions Workload Identity Federation (`google_iam_workload_identity_pool` + `google_iam_workload_identity_pool_provider`) restricted by `assertion.repository == var.github_repository`. |
| `outputs.tf` | Cloud Run service URL, runtime/deployer service account emails, WIF provider name, Model Armor template resource names, and Artifact Registry URI. |
| `terraform.tfvars.example` | Sanitized variable template for local or CI/CD execution. |

## Out-of-Band Secret Population (Rubric #19)

Terraform creates only the empty Secret Manager containers (`ncbi-api-key`, `semantic-scholar-api-key`) so secret payloads never enter `.tfstate` or Git history. Populate versions out-of-band:

```bash
printf "%s" "$NCBI_API_KEY" | gcloud secrets versions add ncbi-api-key --data-file=-
printf "%s" "$SEMANTIC_SCHOLAR_API_KEY" | gcloud secrets versions add semantic-scholar-api-key --data-file=-
```

## Validation & Apply

```bash
terraform -chdir=deployment/terraform fmt -recursive
terraform -chdir=deployment/terraform init -backend=false
terraform -chdir=deployment/terraform validate
```

## Vertex AI Agent Engine Deployment (`adk` & `agents-cli`)

While Terraform provisions the foundational GCP services, IAM, Secret Manager, Model Armor templates, and the stateless Cloud Run v2 runtime, managed **Vertex AI Agent Engine** deployments are executed via the ADK CLI or Google Cloud `agents-cli`:

```bash
# Option A: Google ADK CLI
uv run adk deploy agent_engine \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --region="$GOOGLE_CLOUD_LOCATION" \
  --display_name="alz-recent-research-agent" \
  alz_research_agent

# Option B: Google Cloud Agents CLI
agents-cli deploy \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$GOOGLE_CLOUD_LOCATION" \
  --agent-path alz_research_agent
```
