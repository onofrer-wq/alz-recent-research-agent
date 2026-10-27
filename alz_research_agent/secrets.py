"""Rubric #19 (Secure Secret Management).

Centralized secret resolution module that reads secrets from Google Cloud Secret
Manager in cloud environments and falls back to local environment variables during
local development and offline testing. Never logs secret values.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_SECRET_ENV_MAP: dict[str, str] = {
    "ncbi-api-key": "NCBI_API_KEY",
    "semantic-scholar-api-key": "SEMANTIC_SCHOLAR_API_KEY",
    "google-api-key": "GOOGLE_API_KEY",
}


def get_secret(
    secret_id: str,
    *,
    required: bool = True,
    project_id: str | None = None,
) -> str | None:
    """Resolves a secret by ID from environment variables or GCP Secret Manager.

    Args:
        secret_id: Logical secret identifier (e.g., ``"ncbi-api-key"``).
        required: If ``True``, raises ``RuntimeError`` when the secret cannot be resolved.
        project_id: Optional GCP project ID override.

    Returns:
        Resolved secret string, or ``None`` when ``required=False`` and unavailable.

    Raises:
        RuntimeError: When ``required=True`` and the secret is missing or empty.
    """
    env_key = _SECRET_ENV_MAP.get(secret_id, secret_id.upper().replace("-", "_"))
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val

    app_env = os.environ.get("APP_ENV", "local").strip().lower()
    resolved_project = (project_id if project_id is not None else os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip()

    if app_env in ("dev", "prod") and resolved_project and resolved_project != "your-gcp-project-id":
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{resolved_project}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            payload = response.payload.data.decode("utf-8").strip()
            if payload:
                return payload
        except Exception as exc:
            logger.warning(
                "Secret Manager lookup failed for secret_id=%s (%s)",
                secret_id,
                type(exc).__name__,
                extra={
                    "event": "SECRET_LOOKUP_WARNING",
                    "secret_id": secret_id,
                    "error_type": type(exc).__name__,
                },
            )

    if required:
        raise RuntimeError(f"Required secret '{secret_id}' (env '{env_key}') is not configured.")
    return None
