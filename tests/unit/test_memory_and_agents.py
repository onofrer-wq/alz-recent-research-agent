"""Rubric #5-#10 (Constitution, Compaction, Persistent Session, Async Memory, Multi-Agent & Model Routing).

Unit and end-to-end offline tests for session/memory services, compaction/cache configs,
non-blocking memory consolidation, multi-agent pipeline topology, and a full ADK
``Runner`` execution using a scripted ``BaseLlm`` and SQLite ``DatabaseSessionService``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.memory import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.session import Session
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from pydantic import PrivateAttr

from alz_research_agent.agent import build_app, build_plugins
from alz_research_agent.agents import (
    CONSTITUTION,
    build_orchestrator_agent,
    consult_critique_loop,
    consultant_agent,
    critic_agent,
    orchestrator_agent,
    research_agent,
    research_pipeline,
)
from alz_research_agent.config import (
    AgentRole,
    Settings,
    get_model_for_role,
    get_routing_table,
)
from alz_research_agent.memory import (
    COMPACTION_PROMPT_TEMPLATE,
    build_cache_config,
    build_compaction_config,
    build_memory_service,
    build_session_service,
    drain_background_memory_tasks,
    schedule_memory_consolidation,
)
from alz_research_agent.observability.intent_outcome_plugin import AUDIT_LOGGER_NAME
from alz_research_agent.schemas import ConsultantDraft


class ScriptedFakeLlm(BaseLlm):
    """Offline deterministic ``BaseLlm`` returning scripted tool calls and responses."""

    _seen_requests: list[LlmRequest] = PrivateAttr(default_factory=list)
    _step: int = PrivateAttr(default=0)

    def __init__(self, model: str = "scripted-fake-llm") -> None:
        super().__init__(model=model)

    @property
    def seen_requests(self) -> list[LlmRequest]:
        return self._seen_requests

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream
        self._seen_requests.append(llm_request.model_copy(deep=True))
        tools_offered = set((llm_request.tools_dict or {}).keys())

        # Determine if the latest content is a FunctionResponse
        last_fn_name: str | None = None
        for content in reversed(llm_request.contents or []):
            for part in reversed(content.parts or []):
                fr = getattr(part, "function_response", None)
                if fr is not None and getattr(fr, "name", None):
                    last_fn_name = str(fr.name)
                    break
            if last_fn_name:
                break

        # 1. Orchestrator flow
        if "classify_alzheimers_question_scope" in tools_offered:
            if last_fn_name is None:
                yield _make_function_call_response(
                    "classify_alzheimers_question_scope",
                    {"question": "What are the 2025 plasma p-tau217 biomarker findings in Alzheimer's?"},
                )
                return
            if last_fn_name == "classify_alzheimers_question_scope":
                yield _make_function_call_response(
                    "research_pipeline",
                    {"request": "Investigate 2025 plasma p-tau217 biomarker studies in Alzheimer's."},
                )
                return
            if last_fn_name == "research_pipeline":
                yield _make_function_call_response(
                    "release_final_answer_to_user",
                    {
                        "answer_markdown": (
                            "Recent 2025 peer-reviewed studies (PMID 39000001) demonstrate high "
                            "diagnostic accuracy of plasma p-tau217 for amyloid pathology."
                        )
                    },
                )
                return
            yield _make_text_response(
                "Recent 2025 peer-reviewed studies (PMID 39000001) demonstrate high diagnostic "
                "accuracy of plasma p-tau217 for amyloid pathology in early Alzheimer's disease."
            )
            return

        # 2. Researcher flow
        if "search_pubmed_alzheimers_studies_since" in tools_offered:
            yield _make_text_response(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": "39000001",
                                "title": "2025 Plasma p-tau217 Cohort Study in Early AD",
                                "publication_date": "2025-04-10",
                                "study_type": "observational",
                            }
                        ]
                    }
                )
            )
            return

        # 3. Critic flow
        if "grade_evidence_claim" in tools_offered:
            if last_fn_name is None:
                yield _make_function_call_response(
                    "grade_evidence_claim",
                    {
                        "claim": "Plasma p-tau217 detects amyloid pathology with high accuracy.",
                        "supporting_record_ids": ["39000001"],
                        "study_type": "observational",
                        "studied_population": "early symptomatic Alzheimer patients",
                        "target_population": "early symptomatic Alzheimer patients",
                        "trial_phase": "not_applicable",
                        "sample_size": 420,
                        "source_type": "peer_reviewed",
                    },
                )
                return
            if last_fn_name == "grade_evidence_claim":
                yield _make_function_call_response(
                    "exit_critique_loop",
                    {
                        "reason": "All claims are recent (2025), graded, and properly cited.",
                    },
                )
                return
            yield _make_text_response(
                json.dumps(
                    {
                        "approved": True,
                        "iteration": 1,
                        "issues": [],
                        "required_changes": [],
                        "speculative_extrapolations": [],
                        "newest_findings_emphasised": True,
                        "requires_human_review": False,
                    }
                )
            )
            return

        # 4. Consultant flow (no tools, structured ConsultantDraft JSON)
        draft_payload = {
            "short_answer": (
                "Recent 2025 cohort evidence (PMID 39000001) confirms plasma p-tau217 "
                "accurately tracks amyloid pathology."
            ),
            "claims": [
                {
                    "claim": "Plasma p-tau217 accurately identifies amyloid positivity (PMID 39000001).",
                    "supporting_record_ids": ["39000001"],
                    "study_type": "observational",
                    "trial_phase": "not_applicable",
                    "sample_size": 420,
                    "source_type": "peer_reviewed",
                    "confidence": "moderate",
                    "extrapolation": False,
                    "note": "Observational cohort with N=420.",
                }
            ],
            "whats_new": ["2025 multi-center validation of automated plasma p-tau217 assays."],
            "established_context": ["Blood-based biomarkers reduce reliance on CSF lumbar puncture."],
            "limitations": ["Observational design requires longitudinal follow-up."],
            "references": ["PMID 39000001 - https://pubmed.ncbi.nlm.nih.gov/39000001/"],
        }
        yield _make_text_response(json.dumps(draft_payload))


def _make_function_call_response(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_function_call(name=name, args=args)],
        )
    )


def _make_text_response(text: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=text)],
        )
    )


def test_compaction_and_context_cache_configs() -> None:
    fake_llm = ScriptedFakeLlm()
    comp_cfg = build_compaction_config(llm=fake_llm)
    assert isinstance(comp_cfg, EventsCompactionConfig)
    assert comp_cfg.compaction_interval == 3
    assert comp_cfg.overlap_size == 1
    assert isinstance(comp_cfg.summarizer, LlmEventSummarizer)
    assert "{conversation_history}" in COMPACTION_PROMPT_TEMPLATE
    assert "SPECULATIVE" in COMPACTION_PROMPT_TEMPLATE
    assert "approval_status" in COMPACTION_PROMPT_TEMPLATE

    cache_cfg = build_cache_config()
    assert isinstance(cache_cfg, ContextCacheConfig)
    assert cache_cfg.cache_intervals == 10
    assert cache_cfg.ttl_seconds == 1800
    assert cache_cfg.min_tokens == 2048


def test_session_and_memory_services_local_vs_prod_fail_fast(tmp_path: Path) -> None:
    local_settings = Settings(
        app_env="local",
        session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'local_test.db'}",
    )
    sess_svc = build_session_service(local_settings)
    mem_svc = build_memory_service(local_settings)
    assert isinstance(sess_svc, DatabaseSessionService)
    assert isinstance(mem_svc, InMemoryMemoryService)

    prod_missing_engine = Settings(
        app_env="prod",
        google_cloud_project="alz-prod-project",
        agent_engine_id="",
    )
    with pytest.raises(ValueError, match="AGENT_ENGINE_ID"):
        build_session_service(prod_missing_engine)
    with pytest.raises(ValueError, match="AGENT_ENGINE_ID"):
        build_memory_service(prod_missing_engine)


async def test_background_memory_consolidation_returns_before_completion() -> None:
    completed_flag = False
    stored_sessions: list[Session] = []

    class _SlowMemoryService:
        async def add_session_to_memory(self, session: Session) -> None:
            nonlocal completed_flag
            await asyncio.sleep(0.08)
            stored_sessions.append(session)
            completed_flag = True

    session = Session(
        id="sess-bg-01",
        app_name="alz_research_agent",
        user_id="user-bg",
        state={"caregiver_email": "secret@hospital.org"},
        events=[],
    )
    slow_service = _SlowMemoryService()

    task = schedule_memory_consolidation(session, slow_service)
    assert task is not None
    # Must return immediately before the 80ms sleep completes
    assert completed_flag is False
    assert not task.done()

    drained = await drain_background_memory_tasks(max_wait_seconds=2.0)
    assert drained >= 1
    assert completed_flag is True
    assert len(stored_sessions) == 1
    assert stored_sessions[0].state["caregiver_email"] == "[EMAIL_ADDRESS]"
    assert session.state["caregiver_email"] == "secret@hospital.org"


def test_multi_agent_pipeline_topology_models_and_constitution() -> None:
    routing = get_routing_table()
    assert routing[AgentRole.ORCHESTRATOR.value] != routing[AgentRole.REASONING.value]
    assert "flash" in routing[AgentRole.ORCHESTRATOR.value].lower()
    assert "pro" in routing[AgentRole.REASONING.value].lower()

    assert isinstance(orchestrator_agent, LlmAgent)
    assert isinstance(research_pipeline, SequentialAgent)
    assert isinstance(consult_critique_loop, LoopAgent)
    assert consult_critique_loop.max_iterations == 3

    # Verify sub-agent ordering in SequentialAgent and LoopAgent
    assert research_pipeline.sub_agents[0] is research_agent
    assert research_pipeline.sub_agents[1] is consult_critique_loop
    assert consult_critique_loop.sub_agents[0] is consultant_agent
    assert consult_critique_loop.sub_agents[1] is critic_agent

    # Verify role-based model assignment
    assert orchestrator_agent.model == get_model_for_role(AgentRole.ORCHESTRATOR)
    assert research_agent.model == get_model_for_role(AgentRole.RESEARCH)
    assert consultant_agent.model == get_model_for_role(AgentRole.REASONING)
    assert critic_agent.model == get_model_for_role(AgentRole.CRITIC)

    # Verify every agent instruction starts with CONSTITUTION
    for ag in (orchestrator_agent, research_agent, consultant_agent, critic_agent):
        assert str(ag.instruction).startswith(CONSTITUTION.strip())

    # Verify output_schema and output_keys
    assert research_agent.output_key == "research_findings"
    assert consultant_agent.output_schema is ConsultantDraft
    assert consultant_agent.output_key == "consultant_draft"
    assert critic_agent.output_key == "critic_feedback"
    assert critic_agent.output_schema is None

    # Verify orchestrator tools include AgentTool(research_pipeline) and HITL tools
    orch_tools = orchestrator_agent.tools
    assert any(isinstance(t, AgentTool) and t.agent is research_pipeline for t in orch_tools)
    tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in orch_tools}
    assert "classify_alzheimers_question_scope" in tool_names
    assert "release_final_answer_to_user" in tool_names
    assert "record_human_approval_decision" in tool_names
    assert "load_memory" in tool_names

    # Verify plugin stack ordering
    plugins = build_plugins()
    plugin_names = [p.name for p in plugins]
    assert "medical_safety_policy_plugin" in plugin_names
    assert "recency_policy_plugin" in plugin_names
    assert "self_evaluation_judge_plugin" in plugin_names
    assert "human_approval_gate_plugin" in plugin_names
    assert "reflect_retry_tool_plugin" in plugin_names
    assert "intent_outcome_plugin" in plugin_names
    assert "pii_redaction_plugin" in plugin_names
    assert "memory_consolidation_plugin" in plugin_names


async def test_end_to_end_runner_with_scripted_llm_and_sqlite_session(tmp_path: Path) -> None:
    db_path = tmp_path / "e2e_sessions.db"
    settings = Settings(
        app_env="local",
        session_db_url=f"sqlite+aiosqlite:///{db_path}",
        hitl_mode="state_gate",
    )

    scripted_llm = ScriptedFakeLlm()
    root = build_orchestrator_agent(settings)

    # Substitute ScriptedFakeLlm on root_agent and all nested sub-agents inside AgentTool
    root.model = scripted_llm
    for tool in root.tools:
        if isinstance(tool, AgentTool):
            seq_agent = tool.agent
            for sub in seq_agent.sub_agents:
                if isinstance(sub, LlmAgent):
                    sub.model = scripted_llm
                elif isinstance(sub, LoopAgent):
                    for loop_sub in sub.sub_agents:
                        if isinstance(loop_sub, LlmAgent):
                            loop_sub.model = scripted_llm

    session_service = build_session_service(settings)
    memory_service = build_memory_service(settings)

    consolidated_sessions: list[Session] = []
    orig_add = memory_service.add_session_to_memory

    async def _spy_add_session(sess: Session) -> None:
        consolidated_sessions.append(sess)
        await orig_add(sess)

    memory_service.add_session_to_memory = _spy_add_session  # type: ignore[method-assign]

    e2e_app = build_app(
        settings,
        root=root,
        memory_service=memory_service,
        summarizer_llm=scripted_llm,
    )

    runner = Runner(
        app=e2e_app,
        session_service=session_service,
        memory_service=memory_service,
    )

    captured_audit_records: list[logging.LogRecord] = []

    class _AuditCaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_audit_records.append(record)

    audit_handler = _AuditCaptureHandler()
    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.addHandler(audit_handler)

    try:
        created_session = await session_service.create_session(
            app_name="alz_research_agent",
            user_id="user-e2e-01",
        )
        user_msg = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "What are the 2025 plasma p-tau217 biomarker findings in Alzheimer's? "
                        "Contact me at caregiver.e2e@example.com."
                    )
                )
            ],
        )

        events = []
        async for ev in runner.run_async(
            user_id="user-e2e-01",
            session_id=created_session.id,
            new_message=user_msg,
        ):
            events.append(ev)

        await drain_background_memory_tasks(max_wait_seconds=5.0)

        # 1. Final response arrived with disclaimer
        final_texts = [
            part.text for ev in events if ev.content and ev.content.parts for part in ev.content.parts if part.text
        ]
        assert any("39000001" in t for t in final_texts)
        assert any("not constitute medical advice" in t.lower() for t in final_texts)

        # 2. User email arrived redacted at the LLM
        assert len(scripted_llm.seen_requests) >= 1
        first_req_texts = [
            part.text
            for content in scripted_llm.seen_requests[0].contents
            if content.parts
            for part in content.parts
            if part.text
        ]
        joined_prompt = " ".join(first_req_texts)
        assert "[EMAIL_ADDRESS]" in joined_prompt
        assert "caregiver.e2e@example.com" not in joined_prompt

        # 3. Session persisted in SQLite with events
        persisted = await session_service.get_session(
            app_name="alz_research_agent",
            user_id="user-e2e-01",
            session_id=created_session.id,
        )
        assert persisted is not None
        assert len(persisted.events) >= 2

        # 4. Paired AGENT_INTENT and AGENT_OUTCOME audit logs with agent_id
        intent_events = [r for r in captured_audit_records if getattr(r, "event", None) == "AGENT_INTENT"]
        outcome_events = [r for r in captured_audit_records if getattr(r, "event", None) == "AGENT_OUTCOME"]
        assert len(intent_events) >= 2
        assert len(outcome_events) >= 2

        # 5. Background memory consolidation scheduled and completed
        assert len(consolidated_sessions) >= 1
    finally:
        audit_logger.removeHandler(audit_handler)
        await runner.close()
