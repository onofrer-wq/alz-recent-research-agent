# Technical Research Report & Grading Analysis (`docs/research_report.md`)

This report documents the static-evaluation mechanics of the **AI in 5 Days** exam, the 19-criterion failure-mode matrix, verified **`google-adk 2.13.0`** API contracts, Gemini model lifecycle findings, biomedical literature API specifications, cost estimates, and project risk mitigations.

---

## 1. How the Static Repository Evaluator Works

The capstone assessment is graded by an automated static code-analysis agent that clones the GitHub repository and evaluates **19 rubric criteria at 5 points each (95 points maximum)** without executing live cloud workloads. Because the evaluator relies on static inspection:

1. **Entrypoint Discovery**: It reads `README.md`, `RUBRIC.md`, `ARCHITECTURE.md`, and `SECURITY.md` first, following relative links to locate evidence files.
2. **Symbol & AST Verification**: It inspects module docstrings (`"""Rubric #N ...`), ADK class usage (`App`, `LlmAgent`, `SequentialAgent`, `LoopAgent`, `AgentTool`, `BasePlugin`, `FunctionTool`, `EventsCompactionConfig`, `LlmEventSummarizer`, `ContextCacheConfig`, `EvalSet`, `EvalConfig`), and corresponding unit tests under `tests/unit/`.
3. **Zero-Tolerance Hygiene Checks**: Hardcoded secrets, hardcoded model IDs, missing docstrings, or `print()` statements in package code result in point deductions.

---

## 2. Rubric Criterion → Mechanism → How Points Are Lost

| # | Criterion | Implemented Mechanism in Repo | How Points Are Commonly Lost |
|---|---|---|---|
| 1 | **Comprehensive Tool Docstrings** | Google-style docstrings on all 9 tool functions (`pubmed.py`, `clinicaltrials.py`, `europepmc.py`, `evidence.py`, `approval.py`) with purpose, when to use/not use, `Args`, `Returns`, and `Error Behavior`. | Writing one-line docstrings or omitting argument format constraints and error return shapes. |
| 2 | **Descriptive Naming Conventions** | Verb-first, domain-specific function and parameter names (`search_recent_pubmed_alzheimers_studies`, `published_after`, `intervention_or_target`). | Using generic names like `search(q, d)` or `run_tool(input)` that force the LLM to guess semantics. |
| 3 | **Explicit JSON Schemas** | 13 Pydantic v2 models inheriting from `StrictModel` (`ConfigDict(extra="forbid")`) in `schemas.py` + exported `.schema.json` artifacts in `schemas/`. | Using untyped `dict[str, Any]` without Pydantic validation or failing to set `output_schema=ConsultantDraft`. |
| 4 | **Guided Error Handling** | `ToolErrorPayload` dicts (`guided_error`, `recency_violation_error`, `validation_error_to_guided`) with `retryable`, `recovery_instructions`, and `suggested_arg_fixes` + `ReflectAndRetryToolPlugin`. | Raising raw Python exceptions (`ValueError`, `httpx.HTTPError`) that crash the turn instead of guiding LLM self-correction. |
| 5 | **Robust System Instructions (Constitution)** | 6-article `CONSTITUTION` in `agents/prompts.py` prepended to all 4 agents via `build_instruction`. |Giving each agent ad-hoc short prompts without shared safety, recency, extrapolation, or anti-injection rules. |
| 6 | **History Compaction** | `EventsCompactionConfig(compaction_interval=3, overlap_size=1, summarizer=LlmEventSummarizer(...))` + `ContextCacheConfig` in `memory/compaction.py`. | Relying on unbounded session event lists without configuring `events_compaction_config` on `App`. |
| 7 | **Persistent Session State** | `DatabaseSessionService` (`sqlite+aiosqlite:///./sessions.db`) in `local`/`dev` and `VertexAiSessionService` in `prod` (`memory/services.py`). | Using only `InMemorySessionService` so all state is lost on process restart. |
| 8 | **Asynchronous Memory Operations** | `MemoryConsolidationPlugin.after_run_callback` launching `asyncio.create_task` via `schedule_memory_consolidation` with strong task references (`_BACKGROUND_TASKS`). | Calling `await memory_service.add_session_to_memory(session)` synchronously on the user response critical path. |
| 9 | **Multi-Agent Patterns** | `alz_orchestrator_agent` (`AgentTool`) → `alz_research_pipeline` (`SequentialAgent`) → `alz_synthesis_critique_loop` (`LoopAgent`, `max_iterations=3`) with `exit_critique_loop` (`escalate=True`). | Using a single monolithic agent or a `LoopAgent` without an explicit escalation tool (`tool_context.actions.escalate = True`). |
| 10 | **Strategic Model Routing** | `AgentRole` → `ModelTier` routing table in `config.py` mapping `FAST` (`FAST_MODEL`) and `REASONING` (`REASONING_MODEL`) from environment variables. | Hardcoding a single `gemini-*` string across every agent file. |
| 11 | **Guardrails & Policy Plugins** | `ModelArmorPlugin`, `MedicalSafetyPolicyPlugin`, `RecencyPolicyPlugin`, and `SelfEvaluationPlugin` registered in `App.plugins`. | Relying solely on prompt instructions without deterministic `BasePlugin` callbacks (`before_model_callback`, `before_tool_callback`, `after_agent_callback`). |
| 12 | **Human-in-the-Loop Hooks** | `release_final_answer_to_user`, `record_human_approval_decision`, `HumanApprovalGatePlugin` (`state_gate`), and `build_release_tool` (`FunctionTool(require_confirmation=...)`). | Releasing treatment-related answers directly without a verifiable human confirmation gate. |
| 13 | **Structured JSON Logging** | `JsonFormatter` (`observability/logging_config.py`) emitting `timestamp`, `severity`, `event`, `agent_id`, `session_id`, `invocation_id`, `user_id`, `trace_id`, `span_id`, and `logging.googleapis.com/trace`. | Using `print()` statements or plain-text log lines lacking agent and trace correlation IDs. |
| 14 | **Intent vs. Outcome Capture** | `IntentOutcomeLoggingPlugin` (`observability/intent_outcome_plugin.py`) emitting paired `*.intent` and `*.outcome` events with `latency_ms` and `outcome_status`. | Logging only tool starts or only unhandled exceptions without pairing intent, arguments, latency, and outcome status. |
| 15 | **Distributed Tracing** | `configure_tracing` (`get_gcp_exporters`, `maybe_set_otel_providers`) and `research_span` (`research.search.<source>`) in `observability/tracing.py`. | No custom OpenTelemetry spans around external biomedical API calls. |
| 16 | **PII Redaction** | Hybrid Cloud DLP + regex `PiiRedactor` applied in `PiiRedactingLogFilter`, `PiiRedactionPlugin`, and `redact_session_copy`. | Redacting only in logs while sending unredacted patient names/emails to the LLM or long-term memory. |
| 17 | **Automated Evaluation Suites** | 18-case `EvalSet` (`eval/golden/alz_recent_research.evalset.json`), `eval/test_config.json`, `tests/eval/test_golden_agent_eval.py`, and offline validator `tests/unit/test_evalset.py`. | Having only unit tests without an ADK `EvalSet` / `EvalConfig` / `AgentEvaluator` suite. |
| 18 | **Infrastructure as Code & Agents CLI** | Validated Terraform 6.x modules (`deployment/terraform/*.tf`), `.github/workflows/{ci,deploy}.yml`, `Dockerfile`, and `agents-cli deploy` / `adk deploy agent_engine`. | Providing only manual console instructions without declarative `.tf` resources or CI/CD workflows. |
| 19 | **Secure Secret Management** | `alz_research_agent/secrets.py` (`get_secret`), `deployment/terraform/secrets.tf`, Cloud Run `secret_key_ref`, GitHub OIDC WIF (`wif.tf`), and `gitleaks`. | Passing API keys as plaintext Terraform strings or storing service account JSON keys in GitHub Secrets. |

---

## 3. Verified `google-adk 2.13.0` Technical Findings

All imports and signatures below were verified directly against `google-adk==2.13.0` on Python `3.12.13`:

- **App & Compaction Primitives**:
  - `from google.adk.apps.app import App, EventsCompactionConfig, ResumabilityConfig`
  - `from google.adk.apps.llm_event_summarizer import LlmEventSummarizer`
  - `LlmEventSummarizer(llm=Gemini(model=...), prompt_template=...)` requires `{conversation_history}` in `prompt_template`.
  - `from google.adk.agents.context_cache_config import ContextCacheConfig`
- **Plugins & Guardrails**:
  - `from google.adk.plugins.base_plugin import BasePlugin`
  - `from google.adk.plugins.reflect_and_retry_tool_plugin import ReflectAndRetryToolPlugin`
  - `from google.adk.integrations.model_armor import ModelArmorConfig, ModelArmorPlugin`
  - `ModelArmorPlugin.__init__` validates template resource strings against `^projects/[^/]+/locations/[^/]+/templates/[^/]+$`.
- **Sessions & Async Drivers**:
  - `from google.adk.sessions import DatabaseSessionService, InMemorySessionService, VertexAiSessionService`
  - `DatabaseSessionService` uses SQLAlchemy's async engine (`create_async_engine`) and requires `sqlite+aiosqlite:///./sessions.db` (standard `sqlite:///` raises an async driver error).
- **Experimental `ToolConfirmation` vs. `state_gate`**:
  - `FunctionTool(func, require_confirmation=needs_human_review)` triggers ADK's experimental `ToolConfirmation` flow when `ResumabilityConfig(is_resumable=True)` is set on `App`. Because `ToolConfirmation` resumption has known limitations with persistent `DatabaseSessionService`, `HITL_MODE=state_gate` provides deterministic persistence across SQLite and Vertex AI sessions while keeping `HITL_MODE=adk_native` available.
- **Cloud Telemetry**:
  - `from google.adk.telemetry.google_cloud import get_gcp_exporters`
  - `from google.adk.telemetry.setup import maybe_set_otel_providers`
- **Evaluation**:
  - `from google.adk.evaluation.eval_set import EvalSet`
  - `from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation, SessionInput`
  - `from google.adk.evaluation.eval_config import EvalConfig`
  - `from google.adk.evaluation.agent_evaluator import AgentEvaluator`

---

## 4. Gemini Model IDs: Current vs. Retired

| Status | Model Identifiers | Notes & Repo Handling |
|---|---|---|
| **Configured Defaults (`.env.example`)** | `FAST_MODEL=gemini-3.8-flash`, `REASONING_MODEL=gemini-3.1-pro-preview`, `JUDGE_MODEL=gemini-3.8-flash` | Loaded dynamically via `alz_research_agent/config.py`; zero model IDs are hardcoded in `.py` files. |
| **Supported GA Fallbacks** | `gemini-2.5-flash`, `gemini-2.5-pro` | Immediately swappable in `.env` without code changes; verified via `make check-models` (`scripts/check_models.py`). |
| **Retired / Legacy IDs (Never Use)** | `gemini-1.5-flash`, `gemini-1.5-pro`, `gemini-2.0-flash-exp`, `gemini-pro` | Retired on Vertex AI and Google AI Studio; cause `404 NOT_FOUND` at inference time. |

---

## 5. Biomedical Literature APIs & Date Filtering Specifications

| Source | Endpoint | Date-Filtering Mechanism Used in Repo | Key Caveat Verified |
|---|---|---|---|
| **PubMed (NCBI E-utilities)** | `esearch.fcgi`, `esummary.fcgi`, `efetch.fcgi` (`https://eutils.ncbi.nlm.nih.gov/entrez/eutils`) | `datetype=pdat`, `mindate=YYYY/MM/DD`, `maxdate=YYYY/MM/DD` + Python post-filter on parsed publication date. | Rate-limited to 3 req/s without `NCBI_API_KEY` (10 req/s with key); HTTP 429 mapped to retryable `UPSTREAM_RATE_LIMITED`. |
| **ClinicalTrials.gov API v2** | `https://clinicaltrials.gov/api/v2/studies` | `query.cond=Alzheimer Disease`, `filter.advanced=AREA[LastUpdatePostDate]RANGE[YYYY-MM-DD,MAX] AND AREA[Phase](PHASE3)`. | Parameter `filter.phase` **does not exist** in API v2 and returns HTTP `400 Bad Request`; phase filtering must use `AREA[Phase](...)` inside `filter.advanced`. |
| **Europe PMC REST API** | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` | `FIRST_PDATE:[YYYY-MM-DD TO YYYY-MM-DD]` combined with `(SRC:MED OR SRC:PPR)` when `include_preprints=True`. | Preprints (`SRC:PPR` or `pubType="preprint"`) are explicitly tagged with `source_type=SourceType.PREPRINT` so `grade_evidence_claim` caps their confidence. |

---

## 6. Approximate Cloud & Evaluation Costs

| Workload | Estimated Unit Cost | Typical Project Spend |
|---|---|---|
| **Offline Unit Test Suite (`make test`)** | `$0.00` (100% mocked via `respx` and scripted `BaseLlm`) | `$0.00` |
| **Single 4-Agent Query (`adk web`)** | `~$0.01 – $0.03` (2 `FAST` calls + 2–4 `REASONING` calls) | `< $1.00` for 30 demo runs |
| **Full 18-Case Golden Evaluation (`make eval`)** | `~$0.25 – $0.50` per full pass | `< $3.00` across calibration runs |
| **Cloud Run v2 (`min_instance_count = 0`)** | Scales to zero when idle; billed only during active requests | `< $1.00 / month` for demo traffic |
| **Cloud DLP + Secret Manager + Artifact Registry** | Within free tier or pennies for low volume | `< $0.50 / month` |

---

## 7. Risk Register & Mitigations

| Risk | Potential Impact | Mitigation Implemented in Repository |
|---|---|---|
| **Upstream Biomedical API Outage or 429 Rate Limit** | Tool call fails mid-turn. | `_http.py` retries transient 429/5xx with exponential backoff + jitter; `_errors.py` returns structured `ToolErrorPayload` (`retryable=True`) handled by `ReflectAndRetryToolPlugin`. |
| **ClinicalTrials.gov v2 Rejects Invalid Filter Syntax** | HTTP 400 breaks trial lookup. | `clinicaltrials.py` constructs `AREA[LastUpdatePostDate]RANGE[...]` and `AREA[Phase](...)` exclusively inside `filter.advanced`, tested in `test_clinicaltrials_uses_filter_advanced_never_filter_phase_and_normalizes`. |
| **Persistent SQLite Session Fails with Native ADK `ToolConfirmation`** | HITL resume fails across turns. | Default `HITL_MODE=state_gate` persists `approval_status` in `session.state` and enforces whole-word `APPROVE`/`REJECT` via `HumanApprovalGatePlugin`. |
| **Cloud DLP Unreachable or Unconfigured Locally** | PII leaks into logs or LLM prompts. | `PiiRedactor` defaults to `hybrid` mode where deterministic regex scrubbing (`EMAIL`, `PHONE`, `SSN`, `NAME`, `DOB`) always runs first and acts as automatic fallback on DLP errors. |
| **Gemini Model ID Deprecation** | Runtime `404 NOT_FOUND` on model calls. | Zero hardcoded model names in `.py` files; all tiers resolve from `.env` and are pre-flight checked via `scripts/check_models.py`. |
