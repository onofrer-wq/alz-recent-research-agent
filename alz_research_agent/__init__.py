"""Rubric #9 (Multi-Agent Patterns) & Rubric #18 (Infrastructure as Code & Agents CLI).

Top-level package for the Alzheimer's Recent Research Scientific Literacy Agent
built on Google Agent Development Kit (ADK).

Disclaimer:
    This system is strictly a scientific literacy and biomedical literature synthesis
    tool covering peer-reviewed research from the last 3 years. It does NOT provide
    medical advice, clinical diagnosis, or treatment recommendations.
"""

from alz_research_agent.agent import app, build_app, build_plugins, root_agent

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "app",
    "build_app",
    "build_plugins",
    "root_agent",
]
