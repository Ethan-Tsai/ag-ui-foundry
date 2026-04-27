import contextvars
import logging
import os
import threading
from typing import Any

from agent_framework_foundry import FoundryAgent
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from backend.foundry_agent import _SHARED_STATE_TOOLS, _get_required_env
from backend.foundry_runtime import (
    FileAwareFoundryChatClient,
    attach_download_links_transform,
    extract_forwarded_props_from_session,
)

logger = logging.getLogger(__name__)

# Context variable to store the agentId for the current request.
current_agent_id = contextvars.ContextVar("current_agent_id", default=None)

# Module-level cache for agent list: [{"name": ..., "description": ...}, ...]
_agent_list_cache: list[dict] | None = None
_agent_list_lock = threading.Lock()


def _list_project_agents(endpoint: str) -> list[dict]:
    """Fetch agent info from Foundry, with a process-level cache."""
    global _agent_list_cache
    if _agent_list_cache is not None:
        logger.info("Project agents cache hit count=%d", len(_agent_list_cache))
        return _agent_list_cache

    with _agent_list_lock:
        if _agent_list_cache is not None:
            logger.info("Project agents cache hit after lock count=%d", len(_agent_list_cache))
            return _agent_list_cache

        credential = DefaultAzureCredential()
        try:
            logger.info("Project agents discovery start endpoint=%s", endpoint)
            agents: list[dict] = []
            with AIProjectClient(endpoint=endpoint, credential=credential) as client:
                for agent in client.agents.list():
                    description = getattr(agent, "description", "") or ""
                    metadata = getattr(agent, "metadata", {}) or {}
                    if not description and isinstance(metadata, dict):
                        description = str(metadata.get("description", "") or "")
                    agents.append({"name": agent.name, "description": description or "AI Agent"})

            _agent_list_cache = agents
            logger.info("Project agents discovery success count=%d names=%s", len(agents), [agent["name"] for agent in agents])
            return agents
        except Exception as exc:
            logger.exception("Project agents discovery failed: %s", exc)
            return []
        finally:
            credential.close()


class DynamicRouterAgent:
    """Route each request to the selected Foundry agent via query param agentId."""

    def __init__(self, endpoint: str, default_agent_name: str, agent_version: str | None = None):
        self.endpoint = endpoint
        self.default_agent_name = default_agent_name
        self.agent_version = agent_version
        self._cache: dict[str, FoundryAgent] = {}

        # Identity matches the default agent so CopilotKit can find it.
        self.id = default_agent_name
        self.name = default_agent_name
        self.description = "Dynamic multi-agent router"

        logger.info("Pre-warming default agent: %s", default_agent_name)
        try:
            self._get_or_create_agent(default_agent_name)
            logger.info("Default agent ready: %s", default_agent_name)
        except Exception as exc:
            logger.exception("Failed to pre-warm default agent '%s': %s", default_agent_name, exc)

        # Populate agent list cache in the background.
        threading.Thread(target=self._background_discover, daemon=True).start()

    def _background_discover(self) -> None:
        """Discover all agents in the background so /api/agents is fast."""
        try:
            agents = _list_project_agents(self.endpoint)
            names = [agent["name"] for agent in agents]
            logger.info("Background discovery found %d agents: %s", len(names), names)
        except Exception as exc:
            logger.exception("Background discovery failed: %s", exc)

    def _get_or_create_agent(self, agent_name: str) -> FoundryAgent:
        if agent_name in self._cache:
            logger.info("Foundry agent cache hit agent=%s", agent_name)
            return self._cache[agent_name]

        logger.info("Lazy-init Foundry agent start agent=%s version=%s", agent_name, self.agent_version or "-")
        try:
            credential = DefaultAzureCredential()

            self._cache[agent_name] = FoundryAgent(
                project_endpoint=self.endpoint,
                agent_name=agent_name,
                agent_version=self.agent_version,
                credential=credential,
                tools=_SHARED_STATE_TOOLS,
                client_type=FileAwareFoundryChatClient,
            )
            logger.info("Lazy-init Foundry agent success agent=%s", agent_name)
        except Exception:
            logger.exception("Lazy-init Foundry agent failed agent=%s", agent_name)
            raise

        return self._cache[agent_name]

    def _get_active_agent(self) -> FoundryAgent:
        target_name = current_agent_id.get() or self.default_agent_name
        logger.debug("Resolving active Foundry agent requested=%s default=%s", target_name, self.default_agent_name)
        return self._get_or_create_agent(target_name)

    def create_session(self, *, session_id: str | None = None) -> Any:
        logger.info("Create session requested session_id=%s", session_id or "-")
        return self._get_active_agent().create_session(session_id=session_id)

    def get_session(self, service_session_id: str, *, session_id: str | None = None) -> Any:
        logger.info(
            "Get session requested service_session_id=%s session_id=%s",
            service_session_id,
            session_id or "-",
        )
        return self._get_active_agent().get_session(
            service_session_id=service_session_id,
            session_id=session_id,
        )

    def run(self, messages: Any = None, **kwargs):
        agent = self._get_active_agent()
        forwarded_props = extract_forwarded_props_from_session(kwargs.get("session"))
        options = kwargs.get("options")
        options_dict = dict(options) if isinstance(options, dict) else {}
        if forwarded_props:
            options_dict["agui_forwarded_props"] = forwarded_props
        options_dict["agui_include_code_interpreter_outputs"] = True
        if options_dict:
            kwargs["options"] = options_dict

        file_ids = forwarded_props.get("fileIds") or forwarded_props.get("file_ids") or {}
        file_count = len(file_ids) if isinstance(file_ids, dict) else 0
        logger.info(
            "Routing request to agent=%s file_count=%d option_keys=%s",
            agent.name,
            file_count,
            sorted(options_dict.keys()),
        )
        response = agent.run(messages=messages, **kwargs)
        return attach_download_links_transform(response)


def get_dynamic_router_agent() -> DynamicRouterAgent:
    endpoint = _get_required_env("AZURE_AI_PROJECT_ENDPOINT")
    agent_name = _get_required_env("AZURE_AI_PROJECT_AGENT_NAME")
    agent_version = os.getenv("AZURE_AI_PROJECT_AGENT_VERSION", "").strip() or None
    return DynamicRouterAgent(endpoint, agent_name, agent_version)
