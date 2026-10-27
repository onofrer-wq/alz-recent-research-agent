"""Rubric #11 (Guardrails & Policy Plugins) & Rubric #4 (Guided Error Handling).

App-level defense-in-depth ADK plugin that intercepts calls to the three biomedical
literature search tools and blocks any ``since_date`` older than the 3-year
recency floor before the tool executes.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from google.adk.plugins import BasePlugin

from alz_research_agent.schemas import default_recency_floor
from alz_research_agent.tools._errors import ErrorType, tool_error

if TYPE_CHECKING:
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger("alz_research_agent.audit")

RECENCY_GUARDED_TOOLS: frozenset[str] = frozenset(
    {
        "search_pubmed_alzheimers_studies_since",
        "search_clinicaltrials_alzheimers_trials_since",
        "search_europepmc_alzheimers_preprints_since",
    }
)


class RecencyPolicyPlugin(BasePlugin):
    """ADK ``BasePlugin`` enforcing the 3-year recency floor on all literature search tools."""

    def __init__(self, name: str = "recency_policy_plugin") -> None:
        """Initializes the recency policy plugin."""
        super().__init__(name=name)

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Validates ``since_date`` against ``default_recency_floor()`` before tool execution."""
        del tool_context
        tool_name = getattr(tool, "name", "")
        if tool_name not in RECENCY_GUARDED_TOOLS:
            return None

        floor = default_recency_floor()
        raw_since = (tool_args or {}).get("since_date")

        if isinstance(raw_since, date):
            parsed_since: date | None = raw_since
        elif isinstance(raw_since, str):
            try:
                parsed_since = date.fromisoformat(raw_since.strip())
            except ValueError:
                parsed_since = None
        else:
            parsed_since = None

        if parsed_since is None:
            logger.warning(
                "RecencyPolicyPlugin blocked %s due to malformed since_date=%r",
                tool_name,
                raw_since,
                extra={"event": "POLICY_BLOCK", "rule": "recency_floor", "tool_name": tool_name},
            )
            return tool_error(
                ErrorType.RECENCY_VIOLATION,
                message=(
                    f"RecencyPolicyPlugin rejected since_date={raw_since!r} on {tool_name}: "
                    f"must be a valid ISO date on or after {floor.isoformat()}."
                ),
                recovery_instructions=(
                    f"Pass since_date in ISO format YYYY-MM-DD on or after the 3-year "
                    f"recency floor ({floor.isoformat()})."
                ),
                retryable=True,
                source="recency_policy_plugin",
                details={"recency_floor": floor.isoformat()},
            )

        if parsed_since < floor:
            logger.warning(
                "RecencyPolicyPlugin blocked %s with since_date=%s < floor=%s",
                tool_name,
                parsed_since.isoformat(),
                floor.isoformat(),
                extra={"event": "POLICY_BLOCK", "rule": "recency_floor", "tool_name": tool_name},
            )
            return tool_error(
                ErrorType.RECENCY_VIOLATION,
                message=(
                    f"RecencyPolicyPlugin blocked {tool_name}: since_date "
                    f"{parsed_since.isoformat()} is older than the 3-year recency floor "
                    f"({floor.isoformat()})."
                ),
                recovery_instructions=(
                    f"Update since_date to {floor.isoformat()} or a more recent ISO date and retry the search."
                ),
                retryable=True,
                source="recency_policy_plugin",
                details={
                    "requested_since_date": parsed_since.isoformat(),
                    "recency_floor": floor.isoformat(),
                },
            )

        return None
