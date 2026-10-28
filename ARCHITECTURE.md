# System Architecture (`alz_research_agent`)

This document details the runtime topology, plugin execution order, multi-agent control flow, state contract, 3-layer recency enforcement, memory architecture, safety guardrails, observability pipeline, and cloud deployment model of the **Alzheimer's Recent-Research Scientific Literacy Agent**.

---

## 1. End-to-End Runtime Diagram (Plugins, Agents, Tools & Output Keys)

```text
Incoming User Message
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ ADK App ("alz_research_agent") — alz_research_agent/agent.py                        │
│                                                                                     │
│ Ordered Plugin Pipeline:                                                            │
│  [1] PiiRedactionPlugin           (on_user_message_callback, before_model_callback) │
│  [2] ModelArmorPlugin [optional]  (before_model_callback, after_model_callback)     │
│  [3] MedicalSafetyPolicyPlugin    (before_model_callback)                           │
│  [4] RecencyPolicyPlugin          (before_tool_callback)                            │
│  [5] HumanApprovalGatePlugin      (before_tool_callback)                            │
│  [6] IntentOutcomeLoggingPlugin   (before/after agent, model, tool + errors)        │
│  [7] ReflectAndRetryToolPlugin    (max_retries=2, throw_exception_if_retry_exceeded=False) │
│  [8] SelfEvaluationPlugin         (after_agent_callback)                            │
│  [9] MemoryConsolidationPlugin    (after_run_callback -> asyncio.create_task)       │
└─────────────────────────────────────────┬───────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Root Agent: alz_orchestrator_agent (LlmAgent, Tier: FAST)                           │
│ Module: alz_research_agent/agents/orchestrator.py                                   │
│ Tools:                                                                              │
│  • classify_question_scope            ─► writes state["question_scope"]             │
│  • AgentTool(alz_research_pipeline)   ─► invokes SequentialAgent sub-tree           │
│  • record_human_approval_decision     ─► writes state["approval_status"]            │
│  • release_final_answer_to_user       ─► writes state["final_answer"]               │
└─────────────────────────────────────────┬───────────────────────────────────────────┘
                                          │ (via AgentTool)
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Sub-Pipeline: alz_research_pipeline (SequentialAgent)                               │
│ Module: alz_research_agent/agents/pipeline.py                                       │
│                                                                                     │
│  Step 1: alz_researcher_agent (LlmAgent, Tier: FAST)                                │
│  ├─ Module: alz_research_agent/agents/research_agent.py                             │
│  ├─ output_key: "research_dossier"                                                  │
│  └─ Tools:                                                                          │
│      • search_recent_pubmed_alzheimers_studies   (alz_research_agent/tools/pubmed.py)│
│      • fetch_pubmed_abstracts_by_pmid            (alz_research_agent/tools/pubmed.py)│
│      • search_recent_clinicaltrials_gov_alzheimers (clinicaltrials.py)              │
│      • search_recent_europepmc_alzheimers_literature (europepmc.py)                 │
│                                                                                     │
│  Step 2: alz_synthesis_critique_loop (LoopAgent, max_iterations=3)                  │
│  ├─ Sub-Agent 2a: alz_consultant_agent (LlmAgent, Tier: REASONING)                  │
│  │   ├─ Module: alz_research_agent/agents/consultant_agent.py                       │
│  │   ├─ output_schema: ConsultantDraft (alz_research_agent/schemas.py)              │
│  │   └─ output_key: "consultant_draft"                                              │
│  │                                                                                  │
│  └─ Sub-Agent 2b: alz_evidence_critic_agent (LlmAgent, Tier: REASONING)             │
│      ├─ Module: alz_research_agent/agents/critic_agent.py                           │
│      ├─ output_key: "critic_verdict"                                                │
│      └─ Tools:                                                                      │
│          • grade_evidence_claim (alz_research_agent/tools/evidence.py)              │
│          • exit_critique_loop   (sets tool_context.actions.escalate = True)         │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Multi-Agent Pattern Rationale (Rubric #9)

1. **Coordinator via `AgentTool` (`alz_orchestrator_agent`)**:
   Instead of transferring conversation ownership permanently via `sub_agents`, `alz_orchestrator_agent` wraps `alz_research_pipeline` inside `AgentTool(agent=pipeline)` ([`alz_research_agent/agents/orchestrator.py`](alz_research_agent/agents/orchestrator.py)). This guarantees that control always returns to the orchestrator after the research-and-critique pipeline finishes, so every candidate response must pass through `release_final_answer_to_user` and the human-in-the-loop gate before reaching the user.
2. **Deterministic Ordering via `SequentialAgent` (`alz_research_pipeline`)**:
   `SequentialAgent` ([`alz_research_agent/agents/pipeline.py`](alz_research_agent/agents/pipeline.py)) enforces a hard execution barrier: `alz_researcher_agent` must populate `state["research_dossier"]` from live biomedical APIs before `alz_synthesis_critique_loop` can begin drafting. This prevents the consultant from hallucinating citations prior to retrieval.
3. **Iterative Quality Control via `LoopAgent` (`alz_synthesis_critique_loop`)**:
   `LoopAgent(max_iterations=3)` alternates between `alz_consultant_agent` and `alz_evidence_critic_agent`. The critic inspects every claim against `state["research_dossier"]` using `grade_evidence_claim`. If an optimistic extrapolation is unlabeled or a claim lacks a recent citation, the critic withholds `exit_critique_loop` and writes `required_changes` to `state["critic_verdict"]`, forcing `alz_consultant_agent` to revise on the next iteration. When satisfied, calling `exit_critique_loop` sets `tool_context.actions.escalate = True` and `skip_summarization = True` to exit the loop cleanly.

---

## 3. Strategic Model Routing (Rubric #10)

Defined in [`alz_research_agent/config.py`](alz_research_agent/config.py) (`ModelTier`, `AgentRole`, `get_routing_table`, `get_model_for_role`). No model identifier is hardcoded in Python source.

| `AgentRole` | Assigned `ModelTier` | Environment Variable | Architectural Justification |
|---|---|---|---|
| `AgentRole.ORCHESTRATOR` | `ModelTier.FAST` | `FAST_MODEL` | Low-latency intent classification (`classify_question_scope`), pipeline dispatch, and HITL state gating. |
| `AgentRole.RESEARCHER` | `ModelTier.FAST` | `FAST_MODEL` | High-throughput parallel tool invocation across PubMed, ClinicalTrials.gov, and Europe PMC without heavy narrative synthesis. |
| `AgentRole.CONSULTANT` | `ModelTier.REASONING` | `REASONING_MODEL` | Complex cross-study synthesis, strict adherence to `ConsultantDraft` JSON schema, and nuanced separation of newest vs. established evidence. |
| `AgentRole.CRITIC` | `ModelTier.REASONING` | `REASONING_MODEL` | Methodological auditing of trial phases, sample sizes, and detection of subtle speculative extrapolations. |
| `AgentRole.JUDGE` | `ModelTier.JUDGE` | `JUDGE_MODEL` | Cost-efficient post-synthesis safety and groundedness verification in `SelfEvaluationPlugin`. |
| `AgentRole.SUMMARIZER` | `ModelTier.SUMMARIZER` | `FAST_MODEL` | Fast event-history compaction in `LlmEventSummarizer` (`EventsCompactionConfig`). |

---

## 4. Three-Layer Recency Enforcement (`RECENCY_YEARS = 3`)

The core domain invariant—answering **exclusively** from research published within the last `RECENCY_YEARS` (default 3 years)—is enforced independently across three code layers:

1. **Layer 1 — Pre-Tool Policy Guardrail (`RecencyPolicyPlugin`)**:
   [`alz_research_agent/guardrails/recency_policy_plugin.py`](alz_research_agent/guardrails/recency_policy_plugin.py) intercepts every `before_tool_callback`. If a tool call supplies `published_after` earlier than `compute_recency_window().published_after`, the call is blocked before touching the network and returns a structured `RECENCY_VIOLATION` (`ToolErrorPayload`) containing the exact minimum allowed ISO date.
2. **Layer 2 — Tool Input Validation + Upstream Query Floor + Post-Filtering**:
   Each biomedical search tool ([`pubmed.py`](alz_research_agent/tools/pubmed.py), [`clinicaltrials.py`](alz_research_agent/tools/clinicaltrials.py), [`europepmc.py`](alz_research_agent/tools/europepmc.py)) validates `published_after` via `validate_recency_window`, embeds the date floor into the upstream API query (`mindate`/`maxdate` in NCBI E-utilities, `AREA[LastUpdatePostDate]RANGE[...]` in ClinicalTrials.gov v2, `FIRST_PDATE:[...]` in Europe PMC), and **post-filters** returned records in Python, incrementing `recency_violations_dropped` for any out-of-window record.
3. **Layer 3 — System Constitution + Critic Loop + Self-Evaluation**:
   Article 1 of `CONSTITUTION` ([`alz_research_agent/agents/prompts.py`](alz_research_agent/agents/prompts.py)) and `classify_question_scope` ([`alz_research_agent/tools/evidence.py`](alz_research_agent/tools/evidence.py)) clamp user-requested historical dates to the rolling window, while `alz_evidence_critic_agent` and `SelfEvaluationPlugin` verify that every cited identifier in `ConsultantDraft` exists in the current `research_dossier`.

---

## 5. Session State Contract (Rubric #7)

All inter-agent communication uses explicit keys in `tool_context.state` / `callback_context.state`:

| State Key | Written By | Read By | Schema / Type | Purpose |
|---|---|---|---|---|
| `question_scope` | `classify_question_scope` (`orchestrator`) | `orchestrator`, `researcher` | `QuestionScope` dict | Stores `in_scope`, `stakes`, clamped `effective_published_after`, and `requires_human_review`. |
| `requires_human_review` | `classify_question_scope`, `exit_critique_loop` | `needs_human_review`, `release_final_answer_to_user`, `HumanApprovalGatePlugin` | `bool` | Signals that treatment-related or high-stakes content must pause for human approval. |
| `research_dossier` | `alz_researcher_agent` (`output_key`) | `alz_consultant_agent`, `alz_evidence_critic_agent`, `SelfEvaluationPlugin` | `ResearchDossier` / JSON | Collected studies (`PMID:...`, `NCT...`, `PPR:...`) within the 3-year window. |
| `consultant_draft` | `alz_consultant_agent` (`output_key`) | `alz_evidence_critic_agent`, `alz_orchestrator_agent`, `SelfEvaluationPlugin` | `ConsultantDraft` dict/JSON | Structured synthesis with `claims`, `whats_new`, `established_context`, `limitations`, and `references`. |
| `evidence_grades` | `grade_evidence_claim` (`critic`) | `alz_evidence_critic_agent` | `list[EvidenceGrade]` | Per-claim methodological grades and `[SPECULATIVE EXTRAPOLATION]` flags. |
| `critic_verdict` | `alz_evidence_critic_agent` (`output_key` & `exit_critique_loop`) | `alz_consultant_agent`, `alz_orchestrator_agent`, `SelfEvaluationPlugin`, `needs_human_review` | `CriticVerdict` dict/JSON | Approval status, iteration count, required revisions, and speculative extrapolation notes. |
| `approval_status` | `release_final_answer_to_user`, `record_human_approval_decision` | `release_final_answer_to_user`, `HumanApprovalGatePlugin` | `"pending" \| "approved" \| "rejected" \| "released"` | Tracks the HITL approval lifecycle across conversation turns. |
| `final_answer` | `release_final_answer_to_user` | `alz_orchestrator_agent`, external callers | `str` (Markdown) | Draft or released user-facing Markdown with `MANDATORY_DISCLAIMER`. |
| `self_eval` | `SelfEvaluationPlugin` | Observability / audit logs | `dict[str, Any]` | Groundedness check results (`ungrounded_citations`, `verdict_valid`, optional LLM judge output). |

---

## 6. Memory, Persistence & Context Compaction (Rubrics #6, #7, #8)

- **Session Persistence ([`alz_research_agent/memory/services.py`](alz_research_agent/memory/services.py))**:
  - `local` / `dev`: `DatabaseSessionService(db_url=settings.session_db_url)` backed by `sqlite+aiosqlite:///./sessions.db`.
  - `prod`: `VertexAiSessionService(project=..., location=..., agent_engine_id=...)` with fail-fast validation if `AGENT_ENGINE_ID` is unset.
- **Event History Compaction & Context Caching ([`alz_research_agent/memory/compaction.py`](alz_research_agent/memory/compaction.py))**:
  - `build_compaction_config` returns `EventsCompactionConfig(compaction_interval=3, overlap_size=1, summarizer=LlmEventSummarizer(...))` using `COMPACTION_PROMPT_TEMPLATE`, which preserves user questions, cited study IDs (`PMID`, `NCT`, `DOI`), confidence grades, and pending HITL states while discarding verbose raw tool payloads.
  - `build_cache_config` returns `ContextCacheConfig(cache_intervals=10, ttl_seconds=1800, min_tokens=2048)` to cache the shared `CONSTITUTION` prefix across turns.
- **Asynchronous Background Memory Consolidation ([`alz_research_agent/memory/background.py`](alz_research_agent/memory/background.py))**:
  - `MemoryConsolidationPlugin.after_run_callback` invokes `schedule_memory_consolidation(session, memory_service)`.
  - `schedule_memory_consolidation` deep-copies and scrubs PII from the session (`redact_session_copy`) before scheduling `memory_service.add_session_to_memory(redacted_session)` via `asyncio.create_task`, storing strong task references in `_BACKGROUND_TASKS` with completion/error logging callbacks.

---

## 7. Safety Guardrails & Dual-Path Human-in-the-Loop (Rubrics #11, #12)

- **Pre-Model Screening**:
  1. `PiiRedactionPlugin` ([`alz_research_agent/privacy/pii_plugin.py`](alz_research_agent/privacy/pii_plugin.py)) scrubs PII from incoming user messages and outgoing `LlmRequest` contents.
  2. `ModelArmorPlugin` ([`alz_research_agent/guardrails/model_armor.py`](alz_research_agent/guardrails/model_armor.py)) screens prompts and responses against Google Cloud Model Armor templates when configured.
  3. `MedicalSafetyPolicyPlugin` ([`alz_research_agent/guardrails/medical_policy_plugin.py`](alz_research_agent/guardrails/medical_policy_plugin.py)) deterministically short-circuits emergency symptoms (returning urgent care instructions) and personal dosing/prescribing/diagnostic requests on `alz_orchestrator_agent` before invoking the LLM.
- **Dual-Path Human-in-the-Loop (`HITL_MODE` in [`alz_research_agent/hitl/approval.py`](alz_research_agent/hitl/approval.py))**:
  - **`state_gate` (default, compatible with `DatabaseSessionService` and `VertexAiSessionService`)**: When `needs_human_review` is `True` and `state["approval_status"] != "approved"`, `release_final_answer_to_user` stores the draft in `state["final_answer"]`, sets `state["approval_status"] = "pending"`, and returns an `APPROVAL_REQUIRED` guided error instructing the reviewer to reply `APPROVE` or `REJECT`. `HumanApprovalGatePlugin.before_tool_callback` verifies that `record_human_approval_decision` can only be called when `tool_context.user_content` literally contains whole-word `APPROVE` or `REJECT`.
  - **`adk_native` (`FunctionTool(require_confirmation=True)`)**: Wraps `release_final_answer_to_user` with `require_confirmation=needs_human_review` and `ResumabilityConfig(is_resumable=True)` for native ADK `ToolConfirmation` workflows.

---

## 8. Observability & Distributed Tracing (Rubrics #13, #14, #15)

- **Contextual Correlation ([`alz_research_agent/observability/context.py`](alz_research_agent/observability/context.py))**: `ContextVar` instances track `agent_id`, `session_id`, `invocation_id`, and `user_id` across async boundaries via `set_agent_context` and `agent_scope`.
- **Single-Line Cloud Logging JSON ([`alz_research_agent/observability/logging_config.py`](alz_research_agent/observability/logging_config.py))**: `JsonFormatter` emits structured JSON records containing `timestamp`, `severity`, `logger`, `message`, `event`, the 4 context IDs, `trace_id`, `span_id`, and `logging.googleapis.com/trace`, filtered through `PiiRedactingLogFilter`.
- **Intent vs. Outcome Telemetry ([`alz_research_agent/observability/intent_outcome_plugin.py`](alz_research_agent/observability/intent_outcome_plugin.py))**: `IntentOutcomeLoggingPlugin` logs paired `agent.intent`/`agent.outcome`, `llm.intent`/`llm.outcome`, and `tool.intent`/`tool.outcome` events with wall-clock `latency_ms` and `outcome_status` (`success` vs `guided_error`).
- **OpenTelemetry Spans ([`alz_research_agent/observability/tracing.py`](alz_research_agent/observability/tracing.py))**: `configure_tracing` registers GCP exporters via `get_gcp_exporters` and `maybe_set_otel_providers` when `OTEL_TO_CLOUD=true`. Every biomedical tool wraps upstream HTTP execution in `with research_span(source=..., query=..., published_after=...) as span:`.

---

## 9. Cloud Deployment Topology (Rubrics #18, #19)

Provisioned declaratively in [`deployment/terraform/`](deployment/terraform/) and automated via [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml):

- **Keyless CI/CD**: GitHub Actions authenticates to GCP via Workload Identity Federation ([`deployment/terraform/wif.tf`](deployment/terraform/wif.tf)) scoped to `assertion.repository == var.github_repository`.
- **Container Runtime**: Multi-stage non-root [`Dockerfile`](Dockerfile) pushed to Artifact Registry and deployed to Cloud Run v2 ([`deployment/terraform/runtime.tf`](deployment/terraform/runtime.tf)) running as `alz-agent-runtime` with `NCBI_API_KEY` mounted from Secret Manager (`secret_key_ref`).
- **Managed Agent Runtime**: Deployed to Vertex AI Agent Engine via `agents-cli deploy` or `adk deploy agent_engine`.
