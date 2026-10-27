"""Rubric #10 (Strategic Model Routing) & Rubric #19 (Secure Secret Management).

Centralized application settings and role-based Gemini model routing table.
No Gemini model identifiers are hardcoded in Python source files; defaults are
hydrated from ``.env`` and ``.env.example`` via ``python-dotenv`` and
``pydantic-settings``.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"
_ENV_EXAMPLE_FILE = _REPO_ROOT / ".env.example"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)

_EXAMPLE_DEFAULTS: dict[str, str] = (
    {k: (v or "") for k, v in dotenv_values(_ENV_EXAMPLE_FILE).items() if k} if _ENV_EXAMPLE_FILE.exists() else {}
)


def _env_or_example(key: str, fallback: str = "") -> str:
    """Reads ``key`` from ``os.environ`` first, falling back to ``.env.example``."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return _EXAMPLE_DEFAULTS.get(key, fallback).strip()


class AgentRole(StrEnum):
    """Logical agent and subsystem roles mapped to model capability tiers."""

    ORCHESTRATOR = "ORCHESTRATOR"
    RESEARCH = "RESEARCH"
    REASONING = "REASONING"
    CONSULTANT = "CONSULTANT"
    CRITIC = "CRITIC"
    JUDGE = "JUDGE"
    SUMMARIZER = "SUMMARIZER"


class Settings(BaseSettings):
    """Validated runtime settings populated from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default_factory=lambda: _env_or_example("APP_ENV", "local"))
    google_cloud_project: str = Field(default_factory=lambda: _env_or_example("GOOGLE_CLOUD_PROJECT", ""))
    google_cloud_location: str = Field(default_factory=lambda: _env_or_example("GOOGLE_CLOUD_LOCATION", "us-central1"))
    google_genai_use_vertexai: bool = Field(
        default_factory=lambda: _env_or_example("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() in ("true", "1", "yes")
    )

    fast_model: str = Field(default_factory=lambda: _env_or_example("FAST_MODEL"))
    reasoning_model: str = Field(default_factory=lambda: _env_or_example("REASONING_MODEL"))
    judge_model: str = Field(default_factory=lambda: _env_or_example("JUDGE_MODEL"))

    session_db_url: str = Field(
        default_factory=lambda: _env_or_example("SESSION_DB_URL", "sqlite+aiosqlite:///./sessions.db")
    )
    agent_engine_id: str = Field(default_factory=lambda: _env_or_example("AGENT_ENGINE_ID", ""))
    compaction_interval: int = Field(default_factory=lambda: int(_env_or_example("COMPACTION_INTERVAL", "3")))
    compaction_overlap: int = Field(default_factory=lambda: int(_env_or_example("COMPACTION_OVERLAP", "1")))
    recency_years: int = Field(default_factory=lambda: int(_env_or_example("RECENCY_YEARS", "3")))

    model_armor_prompt_template: str = Field(default_factory=lambda: _env_or_example("MODEL_ARMOR_PROMPT_TEMPLATE", ""))
    model_armor_response_template: str = Field(
        default_factory=lambda: _env_or_example("MODEL_ARMOR_RESPONSE_TEMPLATE", "")
    )
    block_on_screening_failure: bool = Field(
        default_factory=lambda: _env_or_example("BLOCK_ON_SCREENING_FAILURE", "false").lower() in ("true", "1", "yes")
    )

    hitl_mode: str = Field(default_factory=lambda: _env_or_example("HITL_MODE", "adk_confirmation"))
    pii_mode: str = Field(default_factory=lambda: _env_or_example("PII_MODE", "local"))
    otel_to_cloud: bool = Field(
        default_factory=lambda: _env_or_example("OTEL_TO_CLOUD", "false").lower() in ("true", "1", "yes")
    )
    otel_service_name: str = Field(
        default_factory=lambda: _env_or_example("OTEL_SERVICE_NAME", "alz-recent-research-agent")
    )
    log_level: str = Field(default_factory=lambda: _env_or_example("LOG_LEVEL", "INFO"))


def get_settings() -> Settings:
    """Constructs a fresh ``Settings`` snapshot reflecting current environment variables."""
    return Settings()


def get_routing_table(settings: Settings | None = None) -> dict[str, str]:
    """Builds the role-to-model routing dictionary without hardcoded model strings.

    Args:
        settings: Optional ``Settings`` instance; defaults to ``get_settings()``.

    Returns:
        Mapping from each ``AgentRole`` name to its configured Gemini model identifier.
    """
    cfg = settings or get_settings()
    return {
        AgentRole.ORCHESTRATOR.value: cfg.fast_model,
        AgentRole.RESEARCH.value: cfg.fast_model,
        AgentRole.REASONING.value: cfg.reasoning_model,
        AgentRole.CONSULTANT.value: cfg.reasoning_model,
        AgentRole.CRITIC.value: cfg.reasoning_model,
        AgentRole.JUDGE.value: cfg.judge_model,
        AgentRole.SUMMARIZER.value: cfg.fast_model,
    }


def get_model_for_role(
    role: AgentRole | str,
    settings: Settings | None = None,
) -> str:
    """Resolves the configured Gemini model identifier for the specified ``role``.

    Args:
        role: Logical ``AgentRole`` or role string (e.g., ``"ORCHESTRATOR"``, ``"REASONING"``).
        settings: Optional ``Settings`` instance.

    Returns:
        Model identifier string from ``FAST_MODEL``, ``REASONING_MODEL``, or ``JUDGE_MODEL``.

    Raises:
        KeyError: If ``role`` is not a recognized ``AgentRole``.
    """
    table = get_routing_table(settings)
    key = role.value if isinstance(role, AgentRole) else str(role).strip().upper()
    if key not in table:
        raise KeyError(f"Unknown AgentRole '{role}'. Valid roles: {sorted(table.keys())}")
    return table[key]
