# Capstone Demo Video Script (5–7 Minutes) (`docs/video_script.md`)

**Target Duration:** 6 minutes 30 seconds  
**Language:** English  
**Visual Setup:** Split screen with Browser (`adk web` at `http://127.0.0.1:8000` and GitHub repo) + Terminal showing live single-line JSON telemetry logs.

---

## `[0:00 – 0:45]` 1. Problem Statement & Scientific Literacy Mission

- **On Screen:** `README.md` top banner and problem/solution table.
- **Narration:**
  > "Hi everyone. Today I'm presenting the **Alzheimer's Recent-Research Scientific Literacy Agent** (`alz_research_agent`), built with **Google Agent Development Kit (`google-adk` 2.13)** and Python 3.12.
  >
  > When patients, caregivers, or clinicians ask standard LLMs about Alzheimer's disease, they usually get undated textbook summaries that blend twenty-year-old amyloid hypotheses with recent clinical trials, gloss over ARIA safety signals, or extrapolate mouse and Phase 1 data into clinical advice.
  >
  > Our system solves this with three strict invariants enforced in code: first, it answers **exclusively using research published within the last 3 years**; second, every scientific claim is methodologically graded by trial phase, study design, and sample size; and third, it is strictly a scientific literacy tool that pauses behind a **human-in-the-loop approval gate** before releasing any treatment-related synthesis."

---

## `[0:45 – 1:45]` 2. The Four Specialized Agents & Three-Layer Recency Enforcement

- **On Screen:** Open `alz_research_agent/agents/prompts.py` (`CONSTITUTION`), `alz_research_agent/guardrails/recency_policy_plugin.py`, and `alz_research_agent/tools/pubmed.py`.
- **Narration:**
  > "Instead of relying on a single prompt, we coordinate four specialized agents that share a 6-article **System Constitution**:
  > 1. `alz_orchestrator_agent` classifies scope and clinical stakes, clamps date ranges, and owns the user turn.
  > 2. `alz_researcher_agent` collects peer-reviewed studies, registered trials, and preprints from **PubMed E-utilities**, **ClinicalTrials.gov API v2**, and **Europe PMC**—never editorializing.
  > 3. `alz_consultant_agent` synthesizes the dossier into a strict Pydantic `ConsultantDraft` JSON schema separating newest findings from established context.
  > 4. `alz_evidence_critic_agent` audits every claim with `grade_evidence_claim`, tags optimistic jumps as `[SPECULATIVE EXTRAPOLATION]`, and controls loop termination.
  >
  > The 3-year recency rule isn't just a prompt instruction—it is enforced in **three code layers**: `RecencyPolicyPlugin` blocks any tool call with an older `published_after` before it touches the network; each tool embeds the date floor into the upstream API query and post-filters returned records in Python; and the Critic verifies citation grounding."

---

## `[1:45 – 2:45]` 3. Multi-Agent ADK Architecture & Strategic Model Routing

- **On Screen:** Open `ARCHITECTURE.md` topology diagram, `alz_research_agent/agents/pipeline.py`, and `alz_research_agent/config.py`.
- **Narration:**
  > "Let's look at how the ADK primitives fit together in `pipeline.py` and `orchestrator.py`:
  > - The Orchestrator wraps `alz_research_pipeline` inside `AgentTool`. That keeps outer turn control with the Orchestrator so the pipeline cannot bypass our human approval gate.
  > - Inside `alz_research_pipeline`, a `SequentialAgent` guarantees `alz_researcher_agent` populates `state['research_dossier']` before drafting begins.
  > - Drafting runs inside a `LoopAgent` (`max_iterations=3`) pairing the Consultant and the Evidence Critic. Only when every claim is grounded and graded does the Critic invoke `exit_critique_loop`, which sets `tool_context.actions.escalate = True`.
  >
  > In `config.py`, our **Strategic Model Routing** table maps `ORCHESTRATOR`, `RESEARCHER`, `JUDGE`, and `SUMMARIZER` to the low-latency `FAST` tier (`FAST_MODEL`), while reserving the `REASONING` tier (`REASONING_MODEL`) for the Consultant and Critic. Zero model IDs are hardcoded in Python."

---

## `[2:45 – 4:15]` 4. Live Demo in `adk web`: 4-Agent Trace, Evidence Table & HITL Gate

- **On Screen:** Switch to `adk web` (`http://127.0.0.1:8000`) with JSON terminal logs visible below.
- **Action 1 (Treatment-related query triggering 4-agent pipeline + HITL gate):**
  - Enter: *"What do recent Phase 3 trials from the last 3 years show about donanemab and lecanemab in early Alzheimer's disease?"*
- **Narration:**
  > "In `adk web`, watch the live execution trace:
  > 1. `alz_orchestrator_agent` calls `classify_question_scope`, which marks `stakes='treatment_related'` and `requires_human_review=True`.
  > 2. It delegates to `alz_research_pipeline`: `alz_researcher_agent` queries PubMed, ClinicalTrials.gov v2, and Europe PMC in parallel.
  > 3. `alz_consultant_agent` produces a structured `ConsultantDraft`, and `alz_evidence_critic_agent` grades each claim with `grade_evidence_claim` before calling `exit_critique_loop`.
  > 4. When the Orchestrator calls `release_final_answer_to_user`, our HITL gate intercepts it and returns `APPROVAL_REQUIRED`, holding the draft in `state['final_answer']` with `approval_status='pending'`."
- **Action 2 (Human Reviewer Approval):**
  - Enter: *"APPROVE"*
- **Narration:**
  > "Now I reply `APPROVE`. `HumanApprovalGatePlugin` verifies that the human message literally contains whole-word `APPROVE`, allows `record_human_approval_decision`, and releases the complete response—including the graded evidence table, explicit `[SPECULATIVE EXTRAPOLATION]` flags, and the mandatory scientific literacy disclaimer."

---

## `[4:15 – 5:30]` 5. AgentOps Tour: JSON Logs, Cloud Trace, PII, Model Armor, Eval, Terraform & Secrets

- **On Screen:** Cycle through terminal JSON logs, `alz_research_agent/observability/`, `alz_research_agent/privacy/`, `deployment/terraform/`, and `.github/workflows/ci.yml`.
- **Narration:**
  > "Let's tour the production AgentOps stack:
  > - **Structured JSON Logging & Intent/Outcome Capture (Rubrics #13 & #14):** In the terminal, `JsonFormatter` and `IntentOutcomeLoggingPlugin` emit single-line JSON events (`agent.intent`, `tool.intent`, `tool.outcome`) carrying `agent_id`, `session_id`, `invocation_id`, `latency_ms`, and `logging.googleapis.com/trace`. There is not a single `print()` call in the package, enforced by Ruff rule `T20`.
  > - **Distributed Tracing (Rubric #15):** `tracing.py` wires `get_gcp_exporters` into `maybe_set_otel_providers` and wraps every literature query in a `research.search.<source>` OpenTelemetry span.
  > - **Three-Point PII Redaction (Rubric #16):** Our hybrid Cloud DLP + regex `PiiRedactor` scrubs names, emails, phone numbers, SSNs, and DOBs in `PiiRedactingLogFilter` before logging, in `PiiRedactionPlugin` before LLM calls, and via `redact_session_copy` before background memory consolidation.
  > - **Automated Evaluation in CI (Rubric #17):** `scripts/build_golden_evalset.py` generates an 18-case ADK `EvalSet`—10 biomedical research trajectories and 8 adversarial/behavioral safety cases—validated in CI via `tests/unit/test_evalset.py` and `AgentEvaluator`.
  > - **Terraform IaC & Secret Management (Rubrics #18 & #19):** Under `deployment/terraform/`, validated Terraform 6.x modules provision APIs, least-privilege runtime/deployer service accounts, **Model Armor** prompt and response templates, Cloud Run v2 with `NCBI_API_KEY` mounted via `secret_key_ref`, and keyless GitHub Actions **Workload Identity Federation**."

---

## `[5:30 – 6:15]` 6. Walkthrough of the 19-Row Rubric Compliance Table

- **On Screen:** Open `README.md` (`Rubric Compliance` table) and `RUBRIC.md`.
- **Narration:**
  > "In `README.md` and `RUBRIC.md`, every single one of the **19 evaluation criteria** is mapped to its exact source files, class and function symbols, and deterministic unit tests in `tests/unit/`—38 passing tests in total with zero network calls required for CI."

---

## `[6:15 – 6:45]` 7. Honest Limitations & Closing Disclaimer

- **On Screen:** `README.md` (`Known Limitations & Design Trade-offs`).
- **Narration:**
  > "Finally, we document three honest engineering trade-offs: first, because ADK's native `ToolConfirmation` is still experimental with persistent SQLite sessions, we default to `HITL_MODE=state_gate` while supporting `HITL_MODE=adk_native` for in-memory runs; second, because Gemini model IDs evolve, all model names live in `.env` and are checked with `make check-models`; and third, this system is strictly for scientific literacy and never a substitute for professional medical advice. Thank you for watching!"
