"""Rubric #12 (Human-in-the-Loop Hooks).

Human-in-the-loop approval tools, predicate functions, and ``HumanApprovalGatePlugin``.
"""

from alz_research_agent.hitl.approval import (
    SCIENTIFIC_LITERACY_DISCLAIMER,
    HumanApprovalGatePlugin,
    build_release_tool,
    needs_human_review,
    record_human_approval_decision,
    release_final_answer_to_user,
)

__all__ = [
    "HumanApprovalGatePlugin",
    "SCIENTIFIC_LITERACY_DISCLAIMER",
    "build_release_tool",
    "needs_human_review",
    "record_human_approval_decision",
    "release_final_answer_to_user",
]
