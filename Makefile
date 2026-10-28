.RECIPEPREFIX := >
.PHONY: install lint format test eval eval-cli eval-pytest schemas golden web run check-models gcp-bootstrap tf-init tf-plan tf-apply deploy

install:
> uv sync --extra dev
> uv run pre-commit install

lint:
> uv run ruff check .
> uv run ruff format --check .

format:
> uv run ruff check --fix .
> uv run ruff format .

test:
> uv run pytest tests/unit

eval:
> uv run adk eval alz_research_agent eval/golden/alz_recent_research.evalset.json --config_file_path=eval/test_config.json --print_detailed_results

eval-cli:
> agents-cli eval run

eval-pytest:
> uv run pytest tests/eval -m eval

schemas:
> uv run python scripts/export_schemas.py

golden:
> uv run python scripts/build_golden_evalset.py

web:
> uv run adk web --port 8000 --allow_origins="*" --session_service_uri=sqlite:///./sessions.db --memory_service_uri=sqlite:///./memory.db

run:
> uv run adk run alz_research_agent

check-models:
> uv run python scripts/check_models.py

gcp-bootstrap:
> bash scripts/gcp_bootstrap.sh

tf-init:
> terraform -chdir=deployment/terraform init

tf-plan:
> terraform -chdir=deployment/terraform plan

tf-apply:
> terraform -chdir=deployment/terraform apply

deploy:
> agents-cli deploy
