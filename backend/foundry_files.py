"""Foundry file helpers for upload and download APIs."""

from __future__ import annotations

import os
import tempfile
import logging
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


def _get_project_endpoint() -> str:
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("Missing required environment variable: AZURE_AI_PROJECT_ENDPOINT")
    return endpoint


def upload_foundry_file(file_bytes: bytes, file_name: str) -> str:
    """Upload a file to Foundry and return its file ID."""
    endpoint = _get_project_endpoint()
    logger.info("Foundry upload start filename=%s size_bytes=%d endpoint=%s", file_name, len(file_bytes), endpoint)
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)
    openai_client = project_client.get_openai_client()

    suffix = os.path.splitext(file_name)[1] or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(file_bytes)
        temp_path = temp_file.name
    logger.debug("Foundry upload temp file created filename=%s temp_path=%s", file_name, temp_path)

    try:
        with open(temp_path, "rb") as handle:
            uploaded = openai_client.files.create(file=handle, purpose="assistants")
        file_id = str(uploaded.id)
        logger.info("Foundry upload success filename=%s file_id=%s", file_name, file_id)
        return file_id
    except Exception:
        logger.exception("Foundry upload failed filename=%s", file_name)
        raise
    finally:
        try:
            os.remove(temp_path)
            logger.debug("Foundry upload temp file removed filename=%s temp_path=%s", file_name, temp_path)
        except OSError:
            logger.debug("Foundry upload temp file removal skipped filename=%s temp_path=%s", file_name, temp_path)
        try:
            openai_client.close()
        except Exception:
            logger.debug("OpenAI client close failed after upload filename=%s", file_name, exc_info=True)
        try:
            project_client.close()
        except Exception:
            logger.debug("Project client close failed after upload filename=%s", file_name, exc_info=True)
        credential.close()


def get_foundry_file_content(file_id: str, container_id: str | None = None) -> bytes | None:
    """Download file content from Foundry by file ID."""
    endpoint = _get_project_endpoint()
    logger.info(
        "Foundry download start file_id=%s container_id=%s endpoint=%s",
        file_id,
        container_id or "-",
        endpoint,
    )
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)
    openai_client = project_client.get_openai_client()

    def _to_bytes(raw: Any) -> bytes:
        if hasattr(raw, "read"):
            return raw.read()
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if hasattr(raw, "__iter__"):
            return b"".join(raw)
        return bytes(raw)

    try:
        if container_id and container_id.strip():
            content = openai_client.containers.files.content.retrieve(
                container_id=container_id,
                file_id=file_id,
            )
            payload = _to_bytes(content)
            logger.info("Foundry container download success file_id=%s size_bytes=%d", file_id, len(payload))
            return payload

        content = openai_client.files.content(file_id)
        payload = _to_bytes(content)
        logger.info("Foundry file download success file_id=%s size_bytes=%d", file_id, len(payload))
        return payload
    except Exception:
        logger.exception("Foundry download failed file_id=%s container_id=%s", file_id, container_id or "-")
        return None
    finally:
        try:
            openai_client.close()
        except Exception:
            logger.debug("OpenAI client close failed after download file_id=%s", file_id, exc_info=True)
        try:
            project_client.close()
        except Exception:
            logger.debug("Project client close failed after download file_id=%s", file_id, exc_info=True)
        credential.close()
