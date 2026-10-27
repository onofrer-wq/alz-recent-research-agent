"""Export all explicit JSON Schema files into the schemas/ directory."""

from __future__ import annotations

from pathlib import Path

from alz_research_agent.schemas import export_all_schemas


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    schemas_dir = repo_root / "schemas"
    written = export_all_schemas(schemas_dir)
    print(f"Exported {len(written)} JSON schemas to {schemas_dir}")


if __name__ == "__main__":
    main()
