# Antigravity Session Continuation Prompt (`docs/ANTIGRAVITY_KICKOFF.md`)

Copy and paste the prompt below if you ever need to resume work on `alz-recent-research-agent` in a new Antigravity session (for example, to address static grader feedback after a submission).

---

## Copy-Paste Kickoff Prompt

```markdown
We are working on `/home/onofrer/alz-recent-research-agent` (GitHub repository: `https://github.com/onofrer-wq/alz-recent-research-agent`), a Python 3.12 + `google-adk==2.13.0` multi-agent project for Google's internal "AI in 5 Days" assessment.

### Context & Evaluation Mechanism
- The evaluator is a **static code-reading grading agent** that inspects the GitHub repository without executing code and scores **19 rubric criteria × 5 points = 95 points max** (target: 95/95).
- Every mechanism must have a recognizable class/function name, start its module docstring with `"""Rubric #N (<Criterion Name>).`, be covered by deterministic offline unit tests in `tests/unit/`, and be linked from `README.md` (`Rubric Compliance`) and `RUBRIC.md`.

### Domain & 4-Agent Architecture
- An Alzheimer's disease scientific literacy assistant that answers **ONLY** using research published within the **last 3 years** (`RECENCY_YEARS=3`)—never generic textbook summaries and never medical/clinical advice.
- Four specialized agents:
  1. `alz_orchestrator_agent` (`LlmAgent`, Tier: `FAST`): Coordinator that classifies scope/stakes (`classify_question_scope`), invokes `AgentTool(agent=alz_research_pipeline)`, and gates final delivery through `release_final_answer_to_user` + `record_human_approval_decision`.
  2. `alz_researcher_agent` (`LlmAgent`, Tier: `FAST`, `output_key="research_dossier"`): Queries PubMed E-utilities, ClinicalTrials.gov API v2, and Europe PMC (journal + preprints), enforcing the 3-year rolling window.
  3. `alz_consultant_agent` (`LlmAgent`, Tier: `REASONING`, `output_schema=ConsultantDraft`, `output_key="consultant_draft"`): Synthesizes findings inside `LoopAgent(max_iterations=3)`.
  4. `alz_evidence_critic_agent` (`LlmAgent`, Tier: `REASONING`, `output_key="critic_verdict"`): Grades every claim via `grade_evidence_claim`, flags optimistic extrapolations as `[SPECULATIVE EXTRAPOLATION]`, and calls `exit_critique_loop` (`tool_context.actions.escalate = True`).

### Non-Negotiable Engineering Rules
1. **Language**: Talk to me in **Spanish**, but write **100% of code, docstrings, comments, documentation, and git commit messages in English**.
2. **Module Header**: Every Python module in `alz_research_agent/` must begin its docstring with `"""Rubric #N (<Criterion Name>).`
3. **Zero Hardcoded Models or Secrets**: Never hardcode `gemini-*` strings or credentials in `.py` files. Models come from `.env` (`FAST_MODEL`, `REASONING_MODEL`, `JUDGE_MODEL`) via `alz_research_agent/config.py`, and secrets go through `alz_research_agent/secrets.py` (`get_secret`).
4. **Zero `print()` in Package Code**: Enforced by Ruff rule `T20` (`scripts/` and `tests/` are exempted in `pyproject.toml`). All logs go through `JsonFormatter` and `PiiRedactingLogFilter`.
5. **Quality Bar Before Every Commit**:
   - `uv run ruff check .` and `uv run ruff format --check .` must pass.
   - `uv run pytest tests/unit -v` must pass 100% offline.
   - Keep `README.md` (`Rubric Compliance`) and `RUBRIC.md` synchronized with any file or symbol changes.

### Verified `google-adk==2.13.0` Technical Facts
- `App`, `EventsCompactionConfig`, and `ResumabilityConfig` import from `google.adk.apps.app`.
- `LlmEventSummarizer` imports from `google.adk.apps.llm_event_summarizer` and requires `{conversation_history}` in its `prompt_template`.
- `ContextCacheConfig` imports from `google.adk.agents.context_cache_config`.
- `BasePlugin` imports from `google.adk.plugins.base_plugin`; `ReflectAndRetryToolPlugin` imports from `google.adk.plugins.reflect_and_retry_tool_plugin`.
- `ModelArmorPlugin` and `ModelArmorConfig` import from `google.adk.integrations.model_armor` and validate that template names match `projects/{project}/locations/{location}/templates/{template}`.
- `DatabaseSessionService` (`google.adk.sessions`) requires an async SQLAlchemy URL (`sqlite+aiosqlite:///./sessions.db`).
- `FunctionTool(..., require_confirmation=True)` is experimental with persistent `DatabaseSessionService`; therefore `HITL_MODE=state_gate` (via `HumanApprovalGatePlugin`) is the default, and `HITL_MODE=adk_native` is supported for in-memory sessions.
- `get_gcp_exporters` (`google.adk.telemetry.google_cloud`) and `maybe_set_otel_providers` (`google.adk.telemetry.setup`) configure Cloud Trace/Monitoring/Logging export.
- `EvalSet`, `EvalCase`, `Invocation`, `IntermediateData`, `SessionInput`, `EvalConfig`, and `AgentEvaluator` live in `google.adk.evaluation`.
```
