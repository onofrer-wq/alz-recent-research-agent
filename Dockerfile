FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY alz_research_agent ./alz_research_agent

RUN uv sync --frozen --no-dev

EXPOSE 8080

CMD ["sh", "-c", "uv run adk api_server --host=0.0.0.0 --port=${PORT:-8080} --otel_to_cloud ${SESSION_SERVICE_URI:+--session_service_uri=$SESSION_SERVICE_URI} ${MEMORY_SERVICE_URI:+--memory_service_uri=$MEMORY_SERVICE_URI} ."]
