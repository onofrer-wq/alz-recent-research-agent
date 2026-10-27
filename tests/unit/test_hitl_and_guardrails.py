"""Rubric #11 (Guardrails & Policy Plugins) & Rubric #12 (Human-in-the-Loop Hooks).

Unit tests for HITL approval triggers, ``release_final_answer_to_user``,
``record_human_approval_decision``, ``build_release_tool``, ``HumanApprovalGatePlugin``,
``MedicalSafetyPolicyPlugin``, ``RecencyPolicyPlugin``, ``SelfEvaluationJudgePlugin``,
and ``build_model_armor_plugin``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from google.adk.integrations.model_armor import ModelArmorPlugin
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from alz_research_agent.guardrails import (
    MedicalSafetyPolicyPlugin,
    RecencyPolicyPlugin,
    SelfEvaluationJudgePlugin,
    build_guardrail_plugins,
    build_model_armor_plugin,
    compute_citation_groundedness,
)
from alz_research_agent.hitl import (
    HumanApprovalGatePlugin,
    build_release_tool,
    needs_human_review,
    record_human_approval_decision,
    release_final_answer_to_user,
)
from alz_research_agent.schemas import default_recency_floor
from alz_research_agent.tools._errors import ErrorType


def test_needs_human_review_three_triggers() -> None:
    # Trigger 1: state["requires_human_review"] is True
    ctx_state = SimpleNamespace(state={"requires_human_review": True})
    assert needs_human_review("Purely informational summary of p-tau217.", ctx_state) is True

    # Trigger 2a: critic_verdict as dict in state
    ctx_verdict_dict = SimpleNamespace(state={"critic_verdict": {"approved": True, "requires_human_review": True}})
    assert needs_human_review("Informational summary.", ctx_verdict_dict) is True

    # Trigger 2b: critic_verdict as JSON string in state
    ctx_verdict_json = SimpleNamespace(
        state={"critic_verdict": json.dumps({"approved": True, "iteration": 1, "requires_human_review": True})}
    )
    assert needs_human_review("Informational summary.", ctx_verdict_json) is True

    # Trigger 3: text contains dosing/titration/should take/off-label/prescribe
    ctx_clean = SimpleNamespace(state={})
    assert (
        needs_human_review(
            "Patients should start taking 10 mg/kg every two weeks and titrate.",
            ctx_clean,
        )
        is True
    )
    assert (
        needs_human_review(
            "Recent 2025 observational cohorts examined plasma p-tau217 sensitivity.",
            ctx_clean,
        )
        is False
    )


def test_release_final_answer_adds_disclaimer_and_blocks_treatment_until_approved() -> None:
    # Informational release -> adds disclaimer immediately
    info_ctx = SimpleNamespace(state={}, tool_confirmation=None)
    info_res = release_final_answer_to_user(
        "Recent Phase 3 trials (PMID 39000002) studied amyloid clearance.",
        info_ctx,
    )
    assert info_res["status"] == "success"
    assert info_res["approval_status"] == "released"
    assert info_res["human_reviewed"] is False
    assert "not constitute medical advice" in info_res["final_answer"].lower()

    # Treatment-related release -> blocked with APPROVAL_REQUIRED until approved
    tx_ctx = SimpleNamespace(state={"requires_human_review": True}, tool_confirmation=None)
    blocked = release_final_answer_to_user(
        "The patient should start taking 10 mg/kg lecanemab infusions.",
        tx_ctx,
    )
    assert blocked["status"] == "error"
    assert blocked["error_type"] == ErrorType.APPROVAL_REQUIRED.value
    assert "APPROVE" in blocked["recovery_instructions"]
    assert "REJECT" in blocked["recovery_instructions"]
    assert tx_ctx.state["approval_status"] == "pending"

    # Once approved via record_human_approval_decision -> release succeeds with human_reviewed=True
    dec_res = record_human_approval_decision("APPROVE", tx_ctx)
    assert dec_res["status"] == "success"
    assert tx_ctx.state["approval_status"] == "approved"

    released = release_final_answer_to_user(
        "Overview of published lecanemab Phase 3 trial dosing protocols (10 mg/kg).",
        tx_ctx,
    )
    assert released["status"] == "success"
    assert released["human_reviewed"] is True
    assert "not constitute medical advice" in released["final_answer"].lower()


def test_build_release_tool_modes() -> None:
    tool_confirm = build_release_tool("adk_confirmation")
    assert tool_confirm._require_confirmation is needs_human_review

    tool_state = build_release_tool("state_gate")
    assert tool_state._require_confirmation is False


async def test_human_approval_gate_plugin_requires_literal_approve_and_blocks_release() -> None:
    gate = HumanApprovalGatePlugin()
    record_tool = SimpleNamespace(name="record_human_approval_decision")
    release_tool = SimpleNamespace(name="release_final_answer_to_user")

    # Model attempts to self-approve when user did not say APPROVE
    ctx_no_approve = SimpleNamespace(
        state={"approval_status": "pending"},
        user_content=types.Content(
            role="user",
            parts=[types.Part.from_text(text="Tell me more about lecanemab.")],
        ),
    )
    blocked_self = await gate.before_tool_callback(
        tool=record_tool,
        tool_args={"decision": "APPROVE"},
        tool_context=ctx_no_approve,
    )
    assert blocked_self is not None
    assert blocked_self["status"] == "error"
    assert blocked_self["error_type"] == ErrorType.POLICY_BLOCKED.value

    # Human literally replies APPROVE -> gate allows record_human_approval_decision
    ctx_with_approve = SimpleNamespace(
        state={"approval_status": "pending"},
        user_content=types.Content(
            role="user",
            parts=[types.Part.from_text(text="APPROVE")],
        ),
    )
    allowed = await gate.before_tool_callback(
        tool=record_tool,
        tool_args={"decision": "APPROVE"},
        tool_context=ctx_with_approve,
    )
    assert allowed is None

    # Gate blocks release_final_answer_to_user when review is required and unapproved
    ctx_unapproved_release = SimpleNamespace(
        state={"requires_human_review": True},
        tool_confirmation=None,
    )
    blocked_release = await gate.before_tool_callback(
        tool=release_tool,
        tool_args={"answer_markdown": "Draft requiring human sign-off."},
        tool_context=ctx_unapproved_release,
    )
    assert blocked_release is not None
    assert blocked_release["error_type"] == ErrorType.APPROVAL_REQUIRED.value


async def test_medical_safety_policy_plugin_blocks_dosing_and_emergency_but_allows_info_and_subagents() -> None:
    plugin = MedicalSafetyPolicyPlugin()
    orch_ctx = SimpleNamespace(agent_name="alz_orchestrator_agent")
    sub_ctx = SimpleNamespace(agent_name="alz_researcher_agent")

    # 1. Emergency blocked on orchestrator
    em_req = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="My father has chest pain and a seizure right now!")],
            )
        ]
    )
    em_resp = await plugin.before_model_callback(callback_context=orch_ctx, llm_request=em_req)
    assert isinstance(em_resp, LlmResponse)
    assert "911" in em_resp.content.parts[0].text
    assert "not constitute medical advice" in em_resp.content.parts[0].text.lower()

    # 2. Dosing blocked on orchestrator
    dose_req = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="What dose should my mother take and how to titrate?")],
            )
        ]
    )
    dose_resp = await plugin.before_model_callback(callback_context=orch_ctx, llm_request=dose_req)
    assert isinstance(dose_resp, LlmResponse)
    assert "cannot provide individualized medication dosing" in dose_resp.content.parts[0].text

    # 3. Informational allowed on orchestrator
    info_req = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="What are the recent 2025 Phase 3 biomarker results for donanemab?")],
            )
        ]
    )
    assert await plugin.before_model_callback(callback_context=orch_ctx, llm_request=info_req) is None

    # 4. Subagents are untouched even if prompt mentions dosing terms
    assert await plugin.before_model_callback(callback_context=sub_ctx, llm_request=dose_req) is None

    # 5. after_model_callback appends disclaimer to long orchestrator response
    long_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        "Recent 2024-2026 peer-reviewed studies demonstrate that plasma p-tau217 "
                        "correlates strongly with amyloid PET centiloids in early symptomatic "
                        "Alzheimer's disease cohorts across multiple independent centers."
                    )
                )
            ],
        )
    )
    await plugin.after_model_callback(callback_context=orch_ctx, llm_response=long_resp)
    assert "not constitute medical advice" in long_resp.content.parts[0].text.lower()


async def test_recency_policy_plugin_blocks_old_dates() -> None:
    plugin = RecencyPolicyPlugin()
    floor = default_recency_floor().isoformat()
    tool = SimpleNamespace(name="search_pubmed_alzheimers_studies_since")

    blocked = await plugin.before_tool_callback(
        tool=tool,
        tool_args={"query_terms": "amyloid", "since_date": "2019-05-01"},
        tool_context=SimpleNamespace(),
    )
    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["error_type"] == ErrorType.RECENCY_VIOLATION.value
    assert floor in blocked["recovery_instructions"]

    allowed = await plugin.before_tool_callback(
        tool=tool,
        tool_args={"query_terms": "amyloid", "since_date": floor},
        tool_context=SimpleNamespace(),
    )
    assert allowed is None


async def test_self_eval_plugin_groundedness_and_critic_verdict_validation() -> None:
    dossier = {
        "records": [
            {"record_id": "39000001"},
            {"record_id": "NCT06123456"},
        ]
    }
    # 1 known ID ("39000001") + 1 unknown ID ("39999999") -> 0.5 groundedness
    score_half = compute_citation_groundedness(
        "Study 39000001 and study 39999999 showed biomarker shifts.",
        {"research_dossier": dossier},
    )
    assert score_half == 0.5

    plugin = SelfEvaluationJudgePlugin(enable_llm_judge=False)

    # Consultant finishes with 0.0 groundedness -> marks requires_human_review=True
    consultant_ctx = SimpleNamespace(
        agent_name="alz_consultant_agent",
        state={
            "research_dossier": dossier,
            "consultant_draft": "Hallucinated citation 38888888 without dossier match.",
        },
    )
    await plugin.after_agent_callback(
        agent=SimpleNamespace(name="alz_consultant_agent"),
        callback_context=consultant_ctx,
    )
    assert consultant_ctx.state["self_eval_groundedness"] == 0.0
    assert consultant_ctx.state["requires_human_review"] is True

    # Critic finishes with valid embedded JSON verdict
    critic_valid_ctx = SimpleNamespace(
        agent_name="alz_evidence_critic_agent",
        state={
            "critic_verdict": (
                "Here is my verdict:\n```json\n"
                '{"approved": true, "iteration": 1, "issues": [], "required_changes": [], '
                '"speculative_extrapolations": [], "newest_findings_emphasised": true, '
                '"requires_human_review": false}\n```'
            )
        },
    )
    await plugin.after_agent_callback(
        agent=SimpleNamespace(name="alz_evidence_critic_agent"),
        callback_context=critic_valid_ctx,
    )
    assert critic_valid_ctx.state["critic_verdict_valid"] is True
    assert isinstance(critic_valid_ctx.state["critic_verdict"], dict)
    assert critic_valid_ctx.state.get("requires_human_review") is not True

    # Critic finishes with malformed verdict -> flags requires_human_review=True
    critic_bad_ctx = SimpleNamespace(
        agent_name="alz_evidence_critic_agent",
        state={"critic_verdict": "not valid json at all"},
    )
    await plugin.after_agent_callback(
        agent=SimpleNamespace(name="alz_evidence_critic_agent"),
        callback_context=critic_bad_ctx,
    )
    assert critic_bad_ctx.state["critic_verdict_valid"] is False
    assert critic_bad_ctx.state["requires_human_review"] is True


def test_model_armor_optional_in_local_and_built_when_configured(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_ARMOR_PROMPT_TEMPLATE", raising=False)
    monkeypatch.delenv("MODEL_ARMOR_RESPONSE_TEMPLATE", raising=False)
    assert build_model_armor_plugin() is None

    plugins_without_armor = build_guardrail_plugins()
    assert len(plugins_without_armor) == 3

    armor_plugin = build_model_armor_plugin(
        prompt_template_name="projects/alz-prod/locations/us-central1/templates/alz-prompt-shield",
        response_template_name="projects/alz-prod/locations/us-central1/templates/alz-resp-shield",
        block_on_screening_failure=True,
    )
    assert isinstance(armor_plugin, ModelArmorPlugin)
    assert armor_plugin._config.block_on_screening_failure is True

    monkeypatch.setenv(
        "MODEL_ARMOR_PROMPT_TEMPLATE",
        "projects/alz-prod/locations/us-central1/templates/alz-prompt-shield",
    )
    plugins_with_armor = build_guardrail_plugins()
    assert len(plugins_with_armor) == 4
    assert isinstance(plugins_with_armor[0], ModelArmorPlugin)
