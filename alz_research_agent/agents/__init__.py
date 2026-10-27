"""Rubric #9 (Multi-Agent Patterns) & Rubric #5 (Robust System Instructions / Constitution).

Multi-agent topology for the Alzheimer's Recent Research Scientific Literacy Assistant:

```
User
 │
 ▼
alz_orchestrator_agent (LlmAgent, ORCHESTRATOR tier = FAST_MODEL)
 ├── Tool: classify_alzheimers_question_scope
 ├── Tool: load_memory
 ├── Tool: AgentTool(research_pipeline)
 │          │
 │          ▼
 │         research_pipeline (SequentialAgent)
 │          ├── 1. alz_researcher_agent (LlmAgent, RESEARCH tier = FAST_MODEL)
 │          │       ├── search_pubmed_alzheimers_studies_since
 │          │       ├── fetch_pubmed_abstracts_by_pmid
 │          │       ├── search_clinicaltrials_alzheimers_trials_since
 │          │       └── search_europepmc_alzheimers_preprints_since
 │          │       ──► writes state["research_findings"]
 │          │
 │          └── 2. consult_critique_loop (LoopAgent, max_iterations=3)
 │                  ├── alz_consultant_agent (LlmAgent, REASONING tier = REASONING_MODEL)
 │                  │    └── output_schema=ConsultantDraft ──► state["consultant_draft"]
 │                  └── alz_evidence_critic_agent (LlmAgent, REASONING tier = REASONING_MODEL)
 │                       ├── grade_evidence_claim
 │                       └── exit_critique_loop (escalate=True) ──► state["critic_feedback"]
 ├── Tool: record_human_approval_decision (gated by HumanApprovalGatePlugin)
 └── Tool: release_final_answer_to_user (gated by HITL require_confirmation / state_gate)
```
"""

from alz_research_agent.agents.consultant_agent import (
    build_consultant_agent,
    consultant_agent,
    increment_loop_iteration,
)
from alz_research_agent.agents.critic_agent import build_critic_agent, critic_agent
from alz_research_agent.agents.orchestrator import (
    build_orchestrator_agent,
    orchestrator_agent,
)
from alz_research_agent.agents.pipeline import (
    build_consult_critique_loop,
    build_research_pipeline,
    consult_critique_loop,
    research_pipeline,
)
from alz_research_agent.agents.prompts import (
    CONSTITUTION,
    ROLE_PROMPTS,
    build_instruction,
)
from alz_research_agent.agents.research_agent import (
    build_research_agent,
    research_agent,
)

__all__ = [
    "CONSTITUTION",
    "ROLE_PROMPTS",
    "build_consult_critique_loop",
    "build_consultant_agent",
    "build_critic_agent",
    "build_instruction",
    "build_orchestrator_agent",
    "build_research_agent",
    "build_research_pipeline",
    "consult_critique_loop",
    "consultant_agent",
    "critic_agent",
    "increment_loop_iteration",
    "orchestrator_agent",
    "research_agent",
    "research_pipeline",
]
