"""Rubric #17 (Automated Evaluation Suites).

Live/model-backed evaluation suite invoking ADK's ``AgentEvaluator.evaluate``
against ``eval/golden/alz_recent_research.evalset.json``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator

_GOLDEN_DIR = str(Path(__file__).resolve().parent.parent.parent / "eval" / "golden")


@pytest.mark.eval
async def test_golden_agent_eval() -> None:
    await AgentEvaluator.evaluate(
        agent_module="alz_research_agent",
        eval_dataset_file_path_or_dir=_GOLDEN_DIR,
        num_runs=1,
        print_detailed_results=True,
    )
