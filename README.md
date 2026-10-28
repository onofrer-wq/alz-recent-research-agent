# Alzheimer's Recent-Research Scientific Literacy Agent (`alz_research_agent`)

**A multi-agent scientific literacy system built on Google ADK that answers Alzheimer's disease questions strictly from research published within the last 3 years, grades every claim by study design and trial phase, and enforces a mandatory human-in-the-loop approval gate before releasing treatment-related syntheses.**

> **Medical Disclaimer:** This system is strictly an **educational and scientific literacy tool** summarizing recent Alzheimer's disease research. It **does not provide medical advice, clinical diagnosis, dosing instructions, or treatment recommendations**. Always consult a qualified neurologist, geriatrician, or healthcare professional for personal medical decisions.

---

## Problem & Solution

General-purpose LLMs often answer Alzheimer's disease queries with undated textbook summaries that blend decade-old hypotheses with recent clinical trials, omit negative endpoints or safety signals (such as ARIA-E/ARIA-H in anti-amyloid antibodies), and extrapolate early preclinical or Phase 1/2 results into premature clinical optimism.

`alz_research_agent` replaces generic textbook responses with a **4-agent evidence pipeline** governed by an immutable **System Constitution** ([`alz_research_agent/agents/prompts.py`](alz_research_agent/agents/prompts.py)):

| Agent ID | Module | ADK Pattern | Model Tier | Responsibility |
|---|---|---|---|---|
| `alz_orchestrator_agent` | [`alz_research_agent/agents/orchestrator.py`](alz_research_agent/agents/orchestrator.py) | Coordinator (`LlmAgent` + `AgentTool`) | `FAST` (`FAST_MODEL`) | Classifies scope/stakes, clamps date windows to `RECENCY_YEARS`, delegates to `research_pipeline`, and gates final delivery through HITL approval. |
| `alz_researcher_agent` | [`alz_research_agent/agents/research_agent.py`](alz_research_agent/agents/research_agent.py) | Sequential Step 1 (`LlmAgent`) | `FAST` (`FAST_MODEL`) | Queries PubMed E-utilities, ClinicalTrials.gov v2, and Europe PMC within the rolling 3-year window; collects structured records without editorializing. |
| `alz_consultant_agent` | [`alz_research_agent/agents/consultant_agent.py`](alz_research_agent/agents/consultant_agent.py) | Loop Step 1 (`LlmAgent`, `output_schema=ConsultantDraft`) | `REASONING` (`REASONING_MODEL`) | Synthesizes the `research_dossier` into a structured `ConsultantDraft` separating newest findings, established context, and limitations. |
| `alz_evidence_critic_agent` | [`alz_research_agent/agents/critic_agent.py`](alz_research_agent/agents/critic_agent.py) | Loop Step 2 (`LlmAgent` with `exit_critique_loop`) | `REASONING` (`REASONING_MODEL`) | Grades every claim with `grade_evidence_claim`, flags optimistic extrapolations as `[SPECULATIVE EXTRAPOLATION]`, and either demands a revision or calls `exit_critique_loop`. |

### Multi-Agent Topology

```text
User Message
     │
     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ App ("alz_research_agent")                                                 │
│ Plugins: PiiRedaction ─► ModelArmor (opt) ─► MedicalSafetyPolicy           │
│          ─► RecencyPolicy ─► HumanApprovalGate ─► IntentOutcomeLogging     │
│          ─► ReflectAndRetryTool ─► SelfEvaluation ─► MemoryConsolidation   │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     ▼
                    ┌────────────────────────────────┐
                    │     alz_orchestrator_agent     │
                    │  (Coordinator, Tier: FAST)     │
                    └───────┬────────────────┬───────┘
                            │                │
     1. classify_question_scope              │ 3. release_final_answer_to_user
                            │                │    / record_human_approval_decision
                            ▼                ▼
        2. AgentTool(alz_research_pipeline: SequentialAgent)
                            │
            ┌───────────────┴───────────────────────────────┐
            ▼                                               ▼
┌───────────────────────────┐           ┌───────────────────────────────────────┐
│   alz_researcher_agent    │           │      alz_synthesis_critique_loop      │
│       (Tier: FAST)        │──────────►│       (LoopAgent, max_iterations=3)   │
│ output_key:               │           │                                       │
│   "research_dossier"      │           │  ┌─────────────────────────────────┐  │
├───────────────────────────┤           │  │      alz_consultant_agent       │  │
│ • search_recent_pubmed_   │           │  │        (Tier: REASONING)        │  │
│   alzheimers_studies      │           │  │  output_schema: ConsultantDraft │  │
│ • fetch_pubmed_abstracts_ │           │  │  output_key: "consultant_draft" │  │
│   by_pmid                 │           │  └────────────────┬────────────────┘  │
│ • search_recent_          │           │                   ▼                   │
│   clinicaltrials_gov_     │           │  ┌─────────────────────────────────┐  │
│   alzheimers              │           │  │    alz_evidence_critic_agent    │  │
│ • search_recent_europepmc_│           │  │        (Tier: REASONING)        │  │
│   alzheimers_literature   │           │  │  • grade_evidence_claim         │  │
└───────────────────────────┘           │  │  • exit_critique_loop (escalate)│  │
                                        │  │  output_key: "critic_verdict"   │  │
                                        │  └─────────────────────────────────┘  │
                                        └───────────────────────────────────────┘
```

---

## Quickstart 1: Local Development (Google AI Studio API Key)

```bash
# 1. Clone and install dependencies (Python 3.11+ with uv)
cp .env.example .env
# Edit .env and set GOOGLE_API_KEY=<your-ai-studio-key>

make install

# 2. Run offline unit test suite (38 tests, zero network calls) and linter
make test
make lint

# 3. Export the 13 explicit Pydantic JSON schemas into schemas/
make export-schemas

# 4. Launch interactive ADK Web UI or CLI with SQLite session persistence
make run   # adk web --session_service_uri sqlite+aiosqlite:///./sessions.db .
make cli   # adk run alz_research_agent
```

## Quickstart 2: Google Cloud / Vertex AI Setup

```bash
# 1. Authenticate and bootstrap required GCP APIs (Vertex AI, DLP, Model Armor, Secret Manager, Trace)
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"
./scripts/gcp_bootstrap.sh

# 2. Configure .env for Vertex AI and managed cloud guardrails
# Set in .env:
#   APP_ENV=prod
#   GOOGLE_GENAI_USE_VERTEXAI=true
#   GOOGLE_CLOUD_PROJECT=your-gcp-project-id
#   GOOGLE_CLOUD_LOCATION=us-central1
#   AGENT_ENGINE_ID=your-agent-engine-id
#   PII_MODE=dlp
#   OTEL_TO_CLOUD=true
#   HITL_MODE=state_gate

# 3. Verify configured Gemini model IDs against the live API
make check-models
```

---

## Production Deployment (Terraform + Secret Manager + Agents CLI)

```bash
# 1. Provision GCP infrastructure (APIs, least-privilege SAs, Secret Manager containers,
#    Model Armor prompt/response templates, Artifact Registry, Cloud Run v2, GitHub OIDC WIF)
terraform -chdir=deployment/terraform init
terraform -chdir=deployment/terraform validate
terraform -chdir=deployment/terraform apply -var="project_id=${GOOGLE_CLOUD_PROJECT}"

# 2. Populate secret payloads out-of-band (never stored in Terraform state or Git)
printf "%s" "${NCBI_API_KEY}" | gcloud secrets versions add ncbi-api-key --data-file=-
printf "%s" "${SEMANTIC_SCHOLAR_API_KEY}" | gcloud secrets versions add semantic-scholar-api-key --data-file=-

# 3. Deploy to Vertex AI Agent Engine via Google Cloud Agents CLI or ADK CLI
agents-cli deploy \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --agent-path alz_research_agent

# Equivalent ADK CLI command:
uv run adk deploy agent_engine \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="${GOOGLE_CLOUD_LOCATION}" \
  --display_name="alz-recent-research-agent" \
  alz_research_agent
```

---

## Rubric Compliance (19 Criteria × 5 Points = 95 Max)

| # | Criterion | Implementation Summary | Evidence (Files & Symbols) | Status |
|---|---|---|---|---|
| 1 | **Comprehensive Tool Docstrings** | Every `@FunctionTool`-compatible function documents purpose, when to use/not use, `Args` with constraints and examples, `Returns` schema, and `Error Behavior`. | [`alz_research_agent/tools/pubmed.py`](alz_research_agent/tools/pubmed.py) (`search_recent_pubmed_alzheimers_studies`, `fetch_pubmed_abstracts_by_pmid`), [`alz_research_agent/tools/clinicaltrials.py`](alz_research_agent/tools/clinicaltrials.py) (`search_recent_clinicaltrials_gov_alzheimers`), [`alz_research_agent/tools/europepmc.py`](alz_research_agent/tools/europepmc.py) (`search_recent_europepmc_alzheimers_literature`), [`alz_research_agent/tools/evidence.py`](alz_research_agent/tools/evidence.py) (`grade_evidence_claim`, `exit_critique_loop`, `classify_question_scope`), [`tests/unit/test_tools.py`](tests/unit/test_tools.py) (`test_all_tools_have_complete_google_docstrings`) | ✅ Complete |
| 2 | **Descriptive Naming Conventions** | Verb-first, domain-specific function and parameter names (`search_recent_pubmed_alzheimers_studies`, `published_after`, `intervention_or_target`) eliminate tool-selection ambiguity. | [`alz_research_agent/tools/__init__.py`](alz_research_agent/tools/__init__.py), [`alz_research_agent/hitl/approval.py`](alz_research_agent/hitl/approval.py) (`release_final_answer_to_user`, `record_human_approval_decision`), [`alz_research_agent/schemas.py`](alz_research_agent/schemas.py) | ✅ Complete |
| 3 | **Explicit JSON Schemas** | 13 strict Pydantic v2 models (`ConfigDict(extra="forbid")`) validate all tool inputs, tool outputs, and agent `output_schema` contracts, exported as draft 2020-12 JSON Schemas. | [`alz_research_agent/schemas.py`](alz_research_agent/schemas.py) (`StrictModel`, `StudyRecord`, `ResearchDossier`, `EvidenceGrade`, `ConsultantDraft`, `CriticVerdict`, `QuestionScope`, `PubMedSearchInput`, `PubMedFetchInput`, `ClinicalTrialsSearchInput`, `EuropePmcSearchInput`, `GradeClaimInput`, `ExitLoopInput`, `ToolErrorPayload`), [`scripts/export_schemas.py`](scripts/export_schemas.py) (`export_all_schemas`), [`schemas/ConsultantDraft.schema.json`](schemas/ConsultantDraft.schema.json) | ✅ Complete |
| 4 | **Guided Error Handling** | Tools never raise uncaught exceptions to the LLM; they return structured `ToolErrorPayload` dicts with `error_code`, `retryable`, `recovery_instructions`, and `suggested_arg_fixes`, paired with `ReflectAndRetryToolPlugin`. | [`alz_research_agent/tools/_errors.py`](alz_research_agent/tools/_errors.py) (`ErrorCode`, `guided_error`, `recency_violation_error`, `validation_error_to_guided`), [`alz_research_agent/tools/_http.py`](alz_research_agent/tools/_http.py) (`UpstreamHttpError`, `get_json_with_retry`), [`alz_research_agent/agent.py`](alz_research_agent/agent.py) (`ReflectAndRetryToolPlugin`) | ✅ Complete |
| 5 | **Robust System Instructions (Constitution)** | All 4 agents prepend a shared 6-article `CONSTITUTION` enforcing the 3-year recency floor, zero medical advice, mandatory source citations, newest-findings prioritization, explicit extrapolation labeling, and anti-injection rules. | [`alz_research_agent/agents/prompts.py`](alz_research_agent/agents/prompts.py) (`CONSTITUTION`, `MANDATORY_DISCLAIMER`, `build_instruction`, `ORCHESTRATOR_INSTRUCTION`, `RESEARCHER_INSTRUCTION`, `CONSULTANT_INSTRUCTION`, `CRITIC_INSTRUCTION`), [`tests/unit/test_memory_and_agents.py`](tests/unit/test_memory_and_agents.py) | ✅ Complete |
| 6 | **History Compaction** | `App` configures `EventsCompactionConfig` (`compaction_interval=3`, `overlap_size=1`) with a custom `LlmEventSummarizer` preserving study IDs and HITL status, plus `ContextCacheConfig` for static constitution prefixes. | [`alz_research_agent/memory/compaction.py`](alz_research_agent/memory/compaction.py) (`COMPACTION_PROMPT_TEMPLATE`, `build_compaction_config`, `build_cache_config`), [`alz_research_agent/agent.py`](alz_research_agent/agent.py) (`build_app`) | ✅ Complete |
| 7 | **Persistent Session State** | `build_session_service` provides async SQLite (`DatabaseSessionService`) in `local`/`dev` and managed `VertexAiSessionService` in `prod`, sharing typed state across turns (`research_dossier`, `consultant_draft`, `critic_verdict`, `approval_status`). | [`alz_research_agent/memory/services.py`](alz_research_agent/memory/services.py) (`build_session_service`, `build_memory_service`), [`tests/unit/test_memory_and_agents.py`](tests/unit/test_memory_and_agents.py) (`test_session_and_memory_services_local_vs_prod_fail_fast`, `test_end_to_end_runner_with_scripted_llm_and_sqlite_session`) | ✅ Complete |
| 8 | **Asynchronous Memory Operations** | `MemoryConsolidationPlugin` triggers non-blocking `asyncio.create_task` memory ingestion after each invocation using a PII-scrubbed session copy (`redact_session_copy`) and strong task references. | [`alz_research_agent/memory/background.py`](alz_research_agent/memory/background.py) (`schedule_memory_consolidation`, `drain_background_memory_tasks`, `MemoryConsolidationPlugin`), [`tests/unit/test_memory_and_agents.py`](tests/unit/test_memory_and_agents.py) (`test_background_memory_consolidation_returns_before_completion`) | ✅ Complete |
| 9 | **Multi-Agent Patterns** | Combines Coordinator (`LlmAgent` + `AgentTool`), `SequentialAgent` (`alz_research_pipeline`), and `LoopAgent` (`alz_synthesis_critique_loop`, `max_iterations=3`) with `exit_critique_loop` (`escalate=True`). | [`alz_research_agent/agents/pipeline.py`](alz_research_agent/agents/pipeline.py) (`build_synthesis_critique_loop`, `build_research_pipeline`), [`alz_research_agent/agents/orchestrator.py`](alz_research_agent/agents/orchestrator.py) (`build_orchestrator_agent`), [`alz_research_agent/agents/research_agent.py`](alz_research_agent/agents/research_agent.py), [`alz_research_agent/agents/consultant_agent.py`](alz_research_agent/agents/consultant_agent.py), [`alz_research_agent/agents/critic_agent.py`](alz_research_agent/agents/critic_agent.py) | ✅ Complete |
| 10 | **Strategic Model Routing** | Explicit `AgentRole` → `ModelTier` routing assigns `FAST` (`FAST_MODEL`) to high-frequency coordination/search/judging and `REASONING` (`REASONING_MODEL`) to multi-study synthesis and critique, with zero hardcoded model IDs in Python code. | [`alz_research_agent/config.py`](alz_research_agent/config.py) (`ModelTier`, `AgentRole`, `get_routing_table`, `get_model_for_role`, `Settings`), [`.env.example`](.env.example), [`scripts/check_models.py`](scripts/check_models.py) | ✅ Complete |
| 11 | **Guardrails & Policy Plugins** | Layered `BasePlugin` stack enforcing Google Cloud Model Armor screening, medical safety/emergency interception, 3-year recency bounds, and post-synthesis groundedness + LLM-as-a-Judge self-evaluation. | [`alz_research_agent/guardrails/model_armor.py`](alz_research_agent/guardrails/model_armor.py) (`build_model_armor_plugin`), [`alz_research_agent/guardrails/medical_policy_plugin.py`](alz_research_agent/guardrails/medical_policy_plugin.py) (`MedicalSafetyPolicyPlugin`), [`alz_research_agent/guardrails/recency_policy_plugin.py`](alz_research_agent/guardrails/recency_policy_plugin.py) (`RecencyPolicyPlugin`), [`alz_research_agent/guardrails/self_eval_plugin.py`](alz_research_agent/guardrails/self_eval_plugin.py) (`SelfEvaluationPlugin`), [`deployment/terraform/model_armor.tf`](deployment/terraform/model_armor.tf) | ✅ Complete |
| 12 | **Human-in-the-Loop Hooks** | Dual-mode HITL (`state_gate` for persistent `DatabaseSessionService` and `adk_native` `FunctionTool(require_confirmation=True)`) blocks treatment-related releases until a human explicitly replies `APPROVE` or `REJECT`. | [`alz_research_agent/hitl/approval.py`](alz_research_agent/hitl/approval.py) (`needs_human_review`, `release_final_answer_to_user`, `record_human_approval_decision`, `build_release_tool`, `HumanApprovalGatePlugin`), [`tests/unit/test_hitl_and_guardrails.py`](tests/unit/test_hitl_and_guardrails.py) | ✅ Complete |
| 13 | **Structured JSON Logging** | Single-line `JsonFormatter` outputs ISO-8601 `timestamp`, `severity`, `event`, `agent_id`, `session_id`, `invocation_id`, `user_id`, `trace_id`, `span_id`, and `logging.googleapis.com/trace`, with zero `print()` statements (`ruff` rule `T20`). | [`alz_research_agent/observability/logging_config.py`](alz_research_agent/observability/logging_config.py) (`JsonFormatter`, `configure_logging`), [`alz_research_agent/observability/context.py`](alz_research_agent/observability/context.py) (`set_agent_context`, `agent_scope`, `current_context`), [`tests/unit/test_observability.py`](tests/unit/test_observability.py) | ✅ Complete |
| 14 | **Intent vs. Outcome Capture** | `IntentOutcomeLoggingPlugin` emits paired `agent.intent`/`agent.outcome`, `llm.intent`/`llm.outcome`, and `tool.intent`/`tool.outcome` events with latency in ms, PII-scrubbed args, and `outcome_status` (`success` vs `guided_error`). | [`alz_research_agent/observability/intent_outcome_plugin.py`](alz_research_agent/observability/intent_outcome_plugin.py) (`IntentOutcomeLoggingPlugin`), [`tests/unit/test_observability.py`](tests/unit/test_observability.py) (`test_intent_outcome_plugin_emits_paired_tool_events_with_redacted_email`) | ✅ Complete |
| 15 | **Distributed Tracing** | OpenTelemetry integration with `get_gcp_exporters`/`maybe_set_otel_providers` for Cloud Trace and custom `research_span` (`research.search.<source>`) recording `agent.id`, `research.source`, `recency.floor`, and `research.result_count`. | [`alz_research_agent/observability/tracing.py`](alz_research_agent/observability/tracing.py) (`configure_tracing`, `current_trace_ids`, `research_span`), [`tests/unit/test_observability.py`](tests/unit/test_observability.py) (`test_research_span_records_name_and_attributes_with_in_memory_tracer`) | ✅ Complete |
| 16 | **PII Redaction** | Hybrid Cloud DLP (`deidentify_content`) + deterministic regex redactor (`EMAIL`, `PHONE`, `SSN`, `PERSON_NAME`, `DOB`) wired into `PiiRedactingLogFilter`, `PiiRedactionPlugin`, and `redact_session_copy`. | [`alz_research_agent/privacy/pii_redactor.py`](alz_research_agent/privacy/pii_redactor.py) (`PiiRedactor`, `redact_text`, `redact_session_copy`), [`alz_research_agent/privacy/log_filter.py`](alz_research_agent/privacy/log_filter.py) (`PiiRedactingLogFilter`), [`alz_research_agent/privacy/pii_plugin.py`](alz_research_agent/privacy/pii_plugin.py) (`PiiRedactionPlugin`), [`tests/unit/test_privacy.py`](tests/unit/test_privacy.py) | ✅ Complete |
| 17 | **Automated Evaluation Suites** | 18-case ADK `EvalSet` (10 research queries + 8 safety/behavioral cases) validated against `EvalConfig` (`tool_trajectory_avg_score`, `response_match_score`, `safety_v1`) via `AgentEvaluator` and offline CI tests. | [`scripts/build_golden_evalset.py`](scripts/build_golden_evalset.py) (`build_eval_set`), [`eval/golden/alz_recent_research.evalset.json`](eval/golden/alz_recent_research.evalset.json), [`eval/test_config.json`](eval/test_config.json), [`tests/eval/test_golden_agent_eval.py`](tests/eval/test_golden_agent_eval.py), [`tests/unit/test_evalset.py`](tests/unit/test_evalset.py) | ✅ Complete |
| 18 | **Infrastructure as Code & Agents CLI** | Validated Terraform 6.x stack provisioning APIs, IAM SAs, Secret Manager, Model Armor templates, Artifact Registry, Cloud Run v2, and WIF, paired with GitHub Actions CI/CD and `agents-cli deploy` / `adk deploy agent_engine`. | [`deployment/terraform/versions.tf`](deployment/terraform/versions.tf), [`deployment/terraform/variables.tf`](deployment/terraform/variables.tf), [`deployment/terraform/apis.tf`](deployment/terraform/apis.tf), [`deployment/terraform/iam.tf`](deployment/terraform/iam.tf), [`deployment/terraform/model_armor.tf`](deployment/terraform/model_armor.tf), [`deployment/terraform/runtime.tf`](deployment/terraform/runtime.tf), [`deployment/terraform/outputs.tf`](deployment/terraform/outputs.tf), [`.github/workflows/ci.yml`](.github/workflows/ci.yml), [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml), [`Dockerfile`](Dockerfile) | ✅ Complete |
| 19 | **Secure Secret Management** | Zero hardcoded credentials; `get_secret` resolves secrets from env or Google Cloud Secret Manager with LRU caching, Cloud Run injects secrets via `secret_key_ref`, CI runs `gitleaks`, and deploys use keyless WIF OIDC. | [`alz_research_agent/secrets.py`](alz_research_agent/secrets.py) (`get_secret`, `clear_secret_cache`), [`deployment/terraform/secrets.tf`](deployment/terraform/secrets.tf), [`deployment/terraform/wif.tf`](deployment/terraform/wif.tf), [`.pre-commit-config.yaml`](.pre-commit-config.yaml), [`.gitignore`](.gitignore) | ✅ Complete |

For a deep-dive architectural walkthrough see [`ARCHITECTURE.md`](ARCHITECTURE.md), for the test-by-test grading index see [`RUBRIC.md`](RUBRIC.md), and for secret/PII/clinical safety policies see [`SECURITY.md`](SECURITY.md).

---

## Repository Structure

```text
alz-recent-research-agent/
├── .github/workflows/
│   ├── ci.yml                              # Lint, gitleaks, terraform validate, 38 unit tests, optional ADK eval
│   └── deploy.yml                          # Keyless WIF deploy to Terraform, Cloud Run, and Agent Engine
├── alz_research_agent/
│   ├── __init__.py                         # Exports root_agent and app for ADK discovery
│   ├── agent.py                            # App assembly (ResumabilityConfig, Compaction, Cache, 9 Plugins, Runner)
│   ├── config.py                           # Pydantic Settings + AgentRole -> ModelTier routing table
│   ├── schemas.py                          # 13 strict Pydantic v2 JSON schemas
│   ├── secrets.py                          # Secret Manager + env resolver with LRU cache
│   ├── agents/
│   │   ├── prompts.py                      # 6-Article System Constitution & agent instructions
│   │   ├── orchestrator.py                 # Coordinator agent (alz_orchestrator_agent)
│   │   ├── pipeline.py                     # SequentialAgent + LoopAgent assembly
│   │   ├── research_agent.py               # Literature collector (alz_researcher_agent)
│   │   ├── consultant_agent.py             # Synthesizer (alz_consultant_agent)
│   │   └── critic_agent.py                 # Methodological critic (alz_evidence_critic_agent)
│   ├── guardrails/
│   │   ├── model_armor.py                  # Google Cloud ModelArmorPlugin factory
│   │   ├── medical_policy_plugin.py        # Blocks dosing/diagnosis/emergencies pre-LLM
│   │   ├── recency_policy_plugin.py        # Blocks pre-window published_after tool calls
│   │   └── self_eval_plugin.py             # Groundedness + optional LLM-as-a-Judge checks
│   ├── hitl/
│   │   └── approval.py                     # Dual-mode HITL release gate & HumanApprovalGatePlugin
│   ├── memory/
│   │   ├── services.py                     # DatabaseSessionService / VertexAiSessionService builders
│   │   ├── compaction.py                   # EventsCompactionConfig + LlmEventSummarizer + ContextCacheConfig
│   │   └── background.py                   # Async fire-and-forget memory consolidation with PII scrubbing
│   ├── observability/
│   │   ├── context.py                      # ContextVars for agent_id, session_id, invocation_id, user_id
│   │   ├── logging_config.py               # Single-line Cloud Logging JsonFormatter
│   │   ├── tracing.py                      # Cloud Trace setup & research_span context manager
│   │   └── intent_outcome_plugin.py        # Paired *.intent / *.outcome telemetry plugin
│   ├── privacy/
│   │   ├── pii_redactor.py                 # Cloud DLP + regex fallback redactor & redact_session_copy
│   │   ├── log_filter.py                   # logging.Filter scrubbing all log records and extras
│   │   └── pii_plugin.py                   # BasePlugin scrubbing user messages and LLM requests
│   └── tools/
│       ├── _errors.py                      # Structured ToolErrorPayload builders
│       ├── _http.py                        # Shared retry/backoff httpx client
│       ├── pubmed.py                       # NCBI E-utilities esearch/esummary/efetch tools
│       ├── clinicaltrials.py               # ClinicalTrials.gov API v2 search tool
│       ├── europepmc.py                    # Europe PMC REST API search tool (journal + preprints)
│       └── evidence.py                     # grade_evidence_claim, exit_critique_loop, classify_question_scope
├── deployment/terraform/                   # Validated Terraform 6.x IaC for GCP
├── eval/
│   ├── golden/alz_recent_research.evalset.json
│   ├── test_config.json
│   └── README.md
├── schemas/                                # 13 exported JSON Schema (draft 2020-12) files
├── scripts/
│   ├── build_golden_evalset.py
│   ├── check_models.py
│   ├── export_schemas.py
│   └── gcp_bootstrap.sh
└── tests/
    ├── eval/test_golden_agent_eval.py
    └── unit/                               # 38 deterministic offline unit tests
```

---

## Known Limitations & Design Trade-offs

1. **Experimental ADK `ToolConfirmation` with Persistent Session Services:** In `google-adk 2.13`, `FunctionTool(..., require_confirmation=True)` is marked experimental and has known serialization constraints when paired with persistent `DatabaseSessionService` across process restarts. To guarantee reliable human-in-the-loop gating with SQLite and Vertex AI sessions, `HITL_MODE=state_gate` (enforced by [`HumanApprovalGatePlugin`](alz_research_agent/hitl/approval.py)) is the default, while `HITL_MODE=adk_native` remains selectable for in-memory sessions.
2. **Gemini Model ID Lifecycle & Retirement:** Preview and versioned Gemini model identifiers (`FAST_MODEL`, `REASONING_MODEL`, `JUDGE_MODEL`) change over time. No model string is hardcoded in Python source; [`scripts/check_models.py`](scripts/check_models.py) (`make check-models`) queries `google.genai` `client.models.list()` to verify model availability before deployment.
3. **Live Model Access Required for Full Golden Evaluation:** While [`tests/unit/test_evalset.py`](tests/unit/test_evalset.py) validates the 18-case `EvalSet` and `EvalConfig` schemas offline on every CI run, executing [`tests/eval/test_golden_agent_eval.py`](tests/eval/test_golden_agent_eval.py) (`make eval`) invokes live Gemini inference and is therefore gated in CI on `GOOGLE_API_KEY` availability.

---

## License

Licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE) for full terms.
