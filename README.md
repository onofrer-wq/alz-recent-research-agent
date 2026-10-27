# Alzheimer's Recent Research Scientific Literacy Agent (`alz_research_agent`)

> **Scientific Literacy Disclaimer:** This repository implements a multi-agent biomedical literature exploration assistant restricted exclusively to peer-reviewed Alzheimer's disease research published within the **last 3 years**. It is strictly an **educational and scientific literacy tool** and **never provides medical advice, diagnosis, or clinical treatment recommendations**. Any output touching upon therapeutic interventions requires mandatory human-in-the-loop review before release.

---

## Architecture Overview

Built with **Google Agent Development Kit (`google-adk` 2.13)** and **Python 3.12**, the system coordinates four specialized agents:

1. **Orchestrator Agent (`alz_orchestrator_agent`)**: User-facing coordinator managing session state, safety gates, and delegation.
2. **Researcher Agent (`alz_researcher_agent`)**: Data-collection specialist retrieving recent (< 3 years) studies from PubMed and Semantic Scholar.
3. **Consultant Agent (`alz_consultant_agent`)**: Scientific communicator synthesizing findings into structured narratives.
4. **Critic Agent (`alz_evidence_critic_agent`)**: Methodological evaluator scoring study design, trial phase, sample size, and confidence while pushing toward cutting-edge experimental evidence and explicitly labeling optimistic extrapolations as speculative.

---

## Rubric Compliance (19 Criteria × 5 Points = 95 Max)

| # | Criterion | Status | Primary Evidence & Code Links |
|---|-----------|--------|-------------------------------|
| 1 | Comprehensive Tool Docstrings | Pending | *To be linked as tools are implemented* |
| 2 | Descriptive Naming Conventions | In Progress | [`pyproject.toml`](pyproject.toml), [`alz_research_agent/__init__.py`](alz_research_agent/__init__.py) |
| 3 | Explicit JSON Schemas | Pending | [`schemas/`](schemas/) |
| 4 | Guided Error Handling | Pending | *To be linked* |
| 5 | Robust System Instructions (Constitution) | Pending | *To be linked* |
| 6 | History Compaction | Pending | [`.env.example`](.env.example) (`COMPACTION_INTERVAL`, `COMPACTION_OVERLAP`) |
| 7 | Persistent Session State | Pending | [`.env.example`](.env.example) (`SESSION_DB_URL`), [`Makefile`](Makefile) |
| 8 | Asynchronous Memory Operations | Pending | [`Makefile`](Makefile) (`--memory_service_uri`) |
| 9 | Multi-Agent Patterns | Pending | [`alz_research_agent/__init__.py`](alz_research_agent/__init__.py) |
| 10 | Strategic Model Routing | In Progress | [`.env.example`](.env.example) (`FAST_MODEL`, `REASONING_MODEL`, `JUDGE_MODEL`) |
| 11 | Guardrails & Policy Plugins | Pending | [`.env.example`](.env.example) (`MODEL_ARMOR_*`, `BLOCK_ON_SCREENING_FAILURE`) |
| 12 | Human-in-the-Loop Hooks | Pending | [`.env.example`](.env.example) (`HITL_MODE`) |
| 13 | Structured JSON Logging | In Progress | [`pyproject.toml`](pyproject.toml) (`T20` print ban), [`.env.example`](.env.example) |
| 14 | Intent vs. Outcome Capture | Pending | *To be linked* |
| 15 | Distributed Tracing | In Progress | [`pyproject.toml`](pyproject.toml) (`otel-gcp`), [`Dockerfile`](Dockerfile) (`--otel_to_cloud`) |
| 16 | PII Redaction | In Progress | [`pyproject.toml`](pyproject.toml) (`google-cloud-dlp`), [`.env.example`](.env.example) (`PII_MODE`) |
| 17 | Automated Evaluation Suites | In Progress | [`pyproject.toml`](pyproject.toml) (`eval`, `respx`), [`Makefile`](Makefile) (`eval`, `eval-cli`, `eval-pytest`) |
| 18 | Infrastructure as Code & Agents CLI | In Progress | [`Dockerfile`](Dockerfile), [`Makefile`](Makefile), [`deployment/terraform/`](deployment/terraform/) |
| 19 | Secure Secret Management | In Progress | [`.gitignore`](.gitignore), [`.pre-commit-config.yaml`](.pre-commit-config.yaml) (`gitleaks`), [`.env.example`](.env.example) |
