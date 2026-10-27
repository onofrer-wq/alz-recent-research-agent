"""Verify configured Gemini models in .env against visible models in google-genai."""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai

from alz_research_agent.config import get_routing_table, get_settings


def _normalize_model_id(name: str) -> str:
    cleaned = name.strip()
    for prefix in ("models/", "publishers/google/models/"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify configured Gemini model IDs against google-genai visible models."
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Send a minimal generate_content request to each configured model.",
    )
    args = parser.parse_args(argv)

    load_dotenv(".env", override=False)
    settings = get_settings()
    routing_table = get_routing_table(settings)

    print("=== Strategic Model Routing Table ===")
    for role, model_id in routing_table.items():
        print(f"  {role:<15} -> {model_id}")

    use_vertex = settings.google_genai_use_vertexai
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()

    try:
        if use_vertex:
            client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project or None,
                location=settings.google_cloud_location or "global",
            )
        else:
            client = genai.Client(api_key=api_key or None)

        visible_models = [_normalize_model_id(str(m.name)) for m in client.models.list() if getattr(m, "name", None)]
    except Exception as exc:
        print(f"\n[WARN] Could not list models from google-genai ({type(exc).__name__}: {exc})")
        return 0

    visible_set = set(visible_models)
    configured_models = sorted({settings.fast_model, settings.reasoning_model, settings.judge_model})

    print("\n=== Configured Model Availability ===")
    for model_id in configured_models:
        status = "FOUND" if model_id in visible_set else "NOT FOUND"
        print(f"  {model_id:<30} : {status}")
        if args.smoke and model_id in visible_set:
            try:
                resp = client.models.generate_content(
                    model=model_id,
                    contents="Reply with the single word OK.",
                )
                print(f"    [SMOKE OK] {model_id}: {(resp.text or '').strip()[:40]}")
            except Exception as exc:
                print(f"    [SMOKE FAIL] {model_id}: {type(exc).__name__}: {exc}")

    flash_candidates = [m for m in visible_models if "gemini" in m and "flash" in m]
    pro_candidates = [m for m in visible_models if "gemini" in m and "pro" in m]
    print("\n=== Suggested Visible Gemini Models ===")
    print(f"  Flash candidates : {flash_candidates[:5]}")
    print(f"  Pro candidates   : {pro_candidates[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
