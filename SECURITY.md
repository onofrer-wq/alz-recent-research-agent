# Security, Privacy & Clinical Safety Policy (`SECURITY.md`)

This document defines the security controls implemented across **Secret Management (Rubric #19)**, **PII Redaction & Data Privacy (Rubric #16)**, and **Medical & Prompt-Injection Guardrails (Rubrics #5, #11, #12)**.

---

## 1. Secret Management Policy (Rubric #19)

1. **Zero Secrets in Source Control or Terraform State**:
   - No API keys, service account JSON keys, OAuth tokens, or database passwords are stored in Python code, `.env.example`, or Terraform HCL (`deployment/terraform/`).
   - [`.gitignore`](.gitignore) excludes `.env`, `.env.*` (except `.env.example`), `*.tfvars` (except `*.tfvars.example`), and `*.tfstate*`.
2. **Runtime Resolution via Google Cloud Secret Manager (`alz_research_agent/secrets.py`)**:
   - [`get_secret`](alz_research_agent/secrets.py) checks environment variables first (for local development and Cloud Run mounted secrets) and falls back to `google.cloud.secretmanager.SecretManagerServiceClient.access_secret_version` (`projects/{project}/secrets/{id}/versions/latest`) with `@lru_cache` caching and zero secret-value logging.
   - In Cloud Run v2 ([`deployment/terraform/runtime.tf`](deployment/terraform/runtime.tf)), secrets such as `NCBI_API_KEY` are mounted directly from Secret Manager using `value_source.secret_key_ref` (`secret = google_secret_manager_secret.ncbi_api_key.secret_id`, `version = "latest"`).
   - Secret containers ([`deployment/terraform/secrets.tf`](deployment/terraform/secrets.tf)) are provisioned empty by Terraform; secret versions are populated out-of-band via `gcloud secrets versions add`.
3. **Keyless Authentication (ADC & Workload Identity Federation)**:
   - Local developer workflows authenticate via Google Cloud Application Default Credentials (`gcloud auth application-default login`, verified by [`scripts/gcp_bootstrap.sh`](scripts/gcp_bootstrap.sh)).
   - GitHub Actions CI/CD ([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)) authenticates without long-lived JSON keys via **Workload Identity Federation (OIDC)** ([`deployment/terraform/wif.tf`](deployment/terraform/wif.tf)), restricted by `attribute_condition = "assertion.repository == '${var.github_repository}'"`.
4. **Automated Secret Scanning (`gitleaks` & Pre-Commit)**:
   - [`.pre-commit-config.yaml`](.pre-commit-config.yaml) runs `gitleaks` (`v8.21.2`) and `detect-private-key` prior to every commit.
   - [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs `gitleaks/gitleaks-action@v2` on every push and pull request alongside GitHub Push Protection.

---

## 2. Personal Data & PII Protection (Rubric #16)

Because users asking about Alzheimer's disease frequently share sensitive family or patient details (e.g., *"My mother Maria Garcia, DOB 04/12/1948, email..."*), PII is scrubbed **before** reaching logs, LLMs, or long-term memory:

1. **Hybrid Redaction Engine ([`alz_research_agent/privacy/pii_redactor.py`](alz_research_agent/privacy/pii_redactor.py))**:
   - `PiiRedactor` supports three modes (`PII_MODE=regex|dlp|hybrid`):
     - **Cloud DLP**: Calls `DlpServiceClient.deidentify_content` for `PERSON_NAME`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_SOCIAL_SECURITY_NUMBER`, `DATE_OF_BIRTH`, `MEDICAL_RECORD_NUMBER`, and `STREET_ADDRESS`.
     - **Deterministic Regex Fallback**: Always runs first in `hybrid` mode and acts as a fail-safe fallback if Cloud DLP is unreachable, replacing matches with `[REDACTED_EMAIL]`, `[REDACTED_PHONE]`, `[REDACTED_SSN]`, `[REDACTED_NAME]`, and `[REDACTED_DOB]`.
2. **Pre-Logging Redaction ([`alz_research_agent/privacy/log_filter.py`](alz_research_agent/privacy/log_filter.py))**:
   - `PiiRedactingLogFilter` is attached to the root logger by `configure_logging()` ([`alz_research_agent/observability/logging_config.py`](alz_research_agent/observability/logging_config.py)), scrubbing `record.msg`, `record.args`, and all structured `extra` fields before `JsonFormatter` serializes them.
3. **Pre-LLM Redaction ([`alz_research_agent/privacy/pii_plugin.py`](alz_research_agent/privacy/pii_plugin.py))**:
   - `PiiRedactionPlugin` is the first plugin in `App.plugins`, scrubbing both `on_user_message_callback` and `before_model_callback` (`llm_request.contents`) in place.
4. **Pre-Memory Redaction ([`alz_research_agent/memory/background.py`](alz_research_agent/memory/background.py))**:
   - `schedule_memory_consolidation` calls `redact_session_copy(session)` to deep-copy and scrub all session events and state values before invoking `memory_service.add_session_to_memory(redacted_session)`. Long-term memory stores only de-identified scientific literacy preferences and research context.

---

## 3. Medical, Clinical & Prompt-Injection Safety (Rubrics #5, #11, #12)

1. **Immutable System Constitution ([`alz_research_agent/agents/prompts.py`](alz_research_agent/agents/prompts.py))**:
   - Prepended to all four agents via `build_instruction`. Prohibits clinical advice, dosing, diagnosis, or treatment recommendations, mandates the rolling 3-year publication floor, requires explicit `[SPECULATIVE EXTRAPOLATION]` tags on early-stage extrapolations, and instructs agents to treat retrieved abstracts as untrusted data.
2. **Deterministic Pre-LLM Medical Policy Plugin ([`alz_research_agent/guardrails/medical_policy_plugin.py`](alz_research_agent/guardrails/medical_policy_plugin.py))**:
   - `MedicalSafetyPolicyPlugin.before_model_callback` intercepts acute medical emergencies (returning immediate urgent-care guidance) and blocks personal dosing, titration, starting/stopping medication, or diagnostic requests before calling the model.
3. **Google Cloud Model Armor ([`alz_research_agent/guardrails/model_armor.py`](alz_research_agent/guardrails/model_armor.py) & [`deployment/terraform/model_armor.tf`](deployment/terraform/model_armor.tf))**:
   - Screens user prompts and model responses for prompt injection/jailbreak (`pi_and_jailbreak_filter_settings`), malicious URIs, Responsible AI categories (`DANGEROUS`, `HARASSMENT`, `HATE_SPEECH`, `SEXUALLY_EXPLICIT`), and Sensitive Data Protection (`sdp_settings`).
4. **Human-in-the-Loop Gate ([`alz_research_agent/hitl/approval.py`](alz_research_agent/hitl/approval.py))**:
   - Any synthesis flagged as treatment-related by `needs_human_review` (via `state["requires_human_review"]`, `critic_verdict.requires_human_review`, or clinical/dosing regex detection) cannot be released by `release_final_answer_to_user` until a human reviewer explicitly responds `APPROVE` or `REJECT` (enforced by `HumanApprovalGatePlugin`).
5. **Mandatory Scientific Literacy Disclaimer**:
   - `release_final_answer_to_user` automatically appends `MANDATORY_DISCLAIMER` to every released response if not already present.

---

## 4. Reporting a Vulnerability

If you discover a security, privacy, or safety bypass issue in this repository:

1. **Do not open a public GitHub issue containing exploit details or live credentials.**
2. Use GitHub's **Private Vulnerability Reporting** ("Report a vulnerability" under the repository's **Security** tab) or contact the repository maintainer directly via GitHub profile [`@onofrer-wq`](https://github.com/onofrer-wq).
3. Include reproduction steps, affected module/file path, and expected vs. actual guardrail behavior.
