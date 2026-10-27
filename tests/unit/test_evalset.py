"""Rubric #17 (Automated Evaluation Suites).

Offline unit test validating that ``eval/golden/alz_recent_research.evalset.json``
deserializes cleanly via ``EvalSet``, contains at least 15 cases including the
6 core behavioral cases, and that ``eval/test_config.json`` configures the 3
mandatory evaluation criteria.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig
from google.adk.evaluation.eval_set import EvalSet


def test_golden_evalset_and_eval_config_validity() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    evalset_path = repo_root / "eval" / "golden" / "alz_recent_research.evalset.json"
    config_path = repo_root / "eval" / "test_config.json"

    assert evalset_path.exists(), f"Missing golden evalset at {evalset_path}"
    assert config_path.exists(), f"Missing eval config at {config_path}"

    eval_set = EvalSet.model_validate_json(evalset_path.read_text(encoding="utf-8"))
    assert len(eval_set.eval_cases) >= 15

    case_ids = {case.eval_id for case in eval_set.eval_cases}
    required_behavioral_prefixes = (
        "behavior_01_out_of_scope",
        "behavior_02_dosing_request",
        "behavior_03_diagnosis_request",
        "behavior_04_emergency",
        "behavior_05_prompt_injection",
        "behavior_06_treatment_related",
    )
    for prefix in required_behavioral_prefixes:
        assert any(cid.startswith(prefix) for cid in case_ids), (
            f"Missing behavioral eval case matching prefix '{prefix}'"
        )

    eval_config = EvalConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    assert "tool_trajectory_avg_score" in eval_config.criteria
    assert "response_match_score" in eval_config.criteria
    assert "safety_v1" in eval_config.criteria
