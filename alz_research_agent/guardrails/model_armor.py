"""Rubric #11 (Guardrails & Policy Plugins).

Factory for ADK ``ModelArmorPlugin`` backed by Google Cloud Model Armor templates.
Constructs ``ModelArmorPlugin(config=ModelArmorConfig(...))`` when prompt or
response template names are configured, and cleanly returns ``None`` in local
environments without templates.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.adk.integrations.model_armor import ModelArmorConfig, ModelArmorPlugin

logger = logging.getLogger(__name__)


def _qualify_template_name(
    raw_name: str | None,
    *,
    project_id: str | None = None,
    location: str | None = None,
) -> str | None:
    """Ensures a Model Armor template name uses the full ``projects/.../locations/.../templates/...`` path."""
    cleaned = (raw_name or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("projects/"):
        return cleaned

    resolved_project = (project_id if project_id is not None else os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip()
    if not resolved_project or resolved_project == "your-gcp-project-id":
        resolved_project = "alz-default-project"

    resolved_location = (
        location if location is not None else os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    ).strip() or "us-central1"

    return f"projects/{resolved_project}/locations/{resolved_location}/templates/{cleaned}"


def build_model_armor_plugin(
    *,
    prompt_template_name: str | None = None,
    response_template_name: str | None = None,
    block_on_screening_failure: bool | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any = None,
) -> ModelArmorPlugin | None:
    """Constructs an ADK ``ModelArmorPlugin`` when templates are configured, else ``None``.

    Args:
        prompt_template_name: Optional override for ``MODEL_ARMOR_PROMPT_TEMPLATE``.
        response_template_name: Optional override for ``MODEL_ARMOR_RESPONSE_TEMPLATE``.
        block_on_screening_failure: Optional override for ``BLOCK_ON_SCREENING_FAILURE``.
        project_id: Optional GCP project ID used to qualify short template names.
        location: Optional GCP location used to qualify short template names.
        client: Optional pre-built ``ModelArmorAsyncClient`` for testing.

    Returns:
        Configured ``ModelArmorPlugin`` instance, or ``None`` if no templates are set.
    """
    raw_prompt = (
        prompt_template_name if prompt_template_name is not None else os.environ.get("MODEL_ARMOR_PROMPT_TEMPLATE", "")
    )
    raw_response = (
        response_template_name
        if response_template_name is not None
        else os.environ.get("MODEL_ARMOR_RESPONSE_TEMPLATE", "")
    )

    qualified_prompt = _qualify_template_name(
        raw_prompt,
        project_id=project_id,
        location=location,
    )
    qualified_response = _qualify_template_name(
        raw_response,
        project_id=project_id,
        location=location,
    )

    if not qualified_prompt and not qualified_response:
        logger.info(
            "Model Armor templates are not configured; skipping ModelArmorPlugin initialization.",
            extra={"event": "MODEL_ARMOR_SKIPPED"},
        )
        return None

    resolved_block = (
        block_on_screening_failure
        if block_on_screening_failure is not None
        else os.environ.get("BLOCK_ON_SCREENING_FAILURE", "false").strip().lower() in ("true", "1", "yes")
    )

    config = ModelArmorConfig(
        prompt_template_name=qualified_prompt,
        response_template_name=qualified_response,
        block_on_screening_failure=resolved_block,
    )
    logger.info(
        "Initialized ModelArmorPlugin (block_on_screening_failure=%s)",
        resolved_block,
        extra={
            "event": "MODEL_ARMOR_ENABLED",
            "prompt_template_name": qualified_prompt,
            "response_template_name": qualified_response,
            "block_on_screening_failure": resolved_block,
        },
    )
    return ModelArmorPlugin(config=config, client=client)
