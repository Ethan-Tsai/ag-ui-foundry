# Copyright (c) Microsoft. All rights reserved.

"""Foundry-backed agent loader for AG-UI."""

from __future__ import annotations

import logging
import os
from typing import Any

from agent_framework.ag_ui import AgentFrameworkAgent
from agent_framework_foundry import FoundryAgent
from azure.identity import DefaultAzureCredential

from backend.state import (
    PREDICT_STATE_CONFIG,
    STATE_SCHEMA,
    add_data_points,
    update_dashboard_context,
    update_insights,
)

logger = logging.getLogger(__name__)

_SHARED_STATE_TOOLS = [
    update_dashboard_context,
    update_insights,
    add_data_points,
]


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _safe_get_latest_version(project_client: Any, agent_name: str, fallback: Any) -> Any:
    try:
        versions = list(project_client.agents.list_versions(agent_name=agent_name))
        if not versions:
            return fallback
        try:
            return sorted(
                versions,
                key=lambda item: int(getattr(item, "version", "0")),
                reverse=True,
            )[0]
        except Exception:
            return versions[-1]
    except Exception:
        return fallback


def get_agent_metadata(
    endpoint: str,
    credential: Any,
    agent_name: str,
    agent_version: str | None,
) -> dict[str, Any]:
    """Resolve display metadata for an agent from Foundry."""
    from azure.ai.projects import AIProjectClient

    logger.info(
        "Fetching metadata for agent '%s' (version=%s)",
        agent_name,
        agent_version or "latest",
    )

    try:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
            target = None
            for agent in project_client.agents.list():
                if agent.name != agent_name:
                    continue
                if agent_version:
                    target = project_client.agents.get(agent_name, version=agent_version)
                else:
                    target = _safe_get_latest_version(project_client, agent.name, agent)
                break

            if target is None:
                raise ValueError(f"Agent '{agent_name}' not found in registry")

            raw_dict: dict[str, Any] = {}
            if hasattr(target, "as_dict"):
                try:
                    raw_dict = target.as_dict() or {}
                except Exception:
                    raw_dict = {}

            metadata = getattr(target, "metadata", None) or raw_dict.get("metadata") or {}
            definition = getattr(target, "definition", None) or raw_dict.get("definition") or {}

            description = (
                getattr(target, "description", None)
                or raw_dict.get("description")
                or metadata.get("description")
                or "AI Agent"
            )

            instructions = (
                getattr(target, "instructions", None)
                or raw_dict.get("instructions")
                or (definition.get("instructions") if isinstance(definition, dict) else "")
                or "No instructions provided"
            )

            welcome_message = str(metadata.get("welcomeMessage") or f"Welcome to {agent_name}")
            starter_prompts_raw = metadata.get("starterPrompts") or []
            if isinstance(starter_prompts_raw, str):
                starter_prompts = [
                    prompt.strip() for prompt in starter_prompts_raw.split("\n") if prompt.strip()
                ]
            elif isinstance(starter_prompts_raw, list):
                starter_prompts = [
                    str(prompt).strip() for prompt in starter_prompts_raw if str(prompt).strip()
                ]
            else:
                starter_prompts = []

            return {
                "name": agent_name,
                "description": description,
                "instructions": instructions,
                "welcomeMessage": welcome_message,
                "starterPrompts": starter_prompts,
            }
    except Exception as exc:
        logger.exception("Failed to fetch detailed agent metadata for '%s': %s", agent_name, exc)
        return {
            "name": agent_name,
            "description": "Dynamic Agent",
            "instructions": "",
            "welcomeMessage": f"Welcome to {agent_name}",
            "starterPrompts": [],
        }


def foundry_agent() -> AgentFrameworkAgent:
    endpoint = _get_required_env("AZURE_AI_PROJECT_ENDPOINT")
    agent_name = _get_required_env("AZURE_AI_PROJECT_AGENT_NAME")
    agent_version = os.getenv("AZURE_AI_PROJECT_AGENT_VERSION", "").strip() or None

    metadata_credential = DefaultAzureCredential()
    try:
        metadata = get_agent_metadata(endpoint, metadata_credential, agent_name, agent_version)
        description = str(metadata.get("description") or "Dynamic Agent")
    finally:
        metadata_credential.close()

    credential = DefaultAzureCredential()
    agent = FoundryAgent(
        project_endpoint=endpoint,
        agent_name=agent_name,
        agent_version=agent_version,
        credential=credential,
        tools=_SHARED_STATE_TOOLS,
    )

    return AgentFrameworkAgent(
        agent=agent,
        name="foundry_agent",
        description=description,
        state_schema=STATE_SCHEMA,
        predict_state_config=PREDICT_STATE_CONFIG,
        require_confirmation=False,
    )
