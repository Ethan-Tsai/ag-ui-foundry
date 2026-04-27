from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AutoCodeInterpreterToolParam,
    CodeInterpreterTool,
    MCPTool,
    PromptAgentDefinition,
    StructuredInputDefinition,
)
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (
    AzureDeveloperCliCredential,
    CredentialUnavailableError,
    DefaultAzureCredential,
)
from dotenv import load_dotenv

load_dotenv()

DEFAULT_FABRIC_MCP_URL = "https://api.fabric.microsoft.com/v1/mcp/powerbi"
DEFAULT_FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_AGENT_NAME = "ada-pbiqa-restapi-poc"
DEFAULT_FILE_SLOTS = 5

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("agent_builder")


def _clean_env(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    text = _clean_env(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean value: {value!r}")


def _must_http_url(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    if not (value.startswith("https://") or value.startswith("http://")):
        raise RuntimeError(f"{name} must be an http(s) URL, got: {value!r}")
    return value


def _parse_csv(value: str | None) -> list[str]:
    text = _clean_env(value)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_json_headers(value: str | None) -> dict[str, str]:
    text = _clean_env(value)
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FOUNDRY_MCP_HEADERS_JSON must be valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("FOUNDRY_MCP_HEADERS_JSON must be a JSON object.")

    headers: dict[str, str] = {}
    for key, val in parsed.items():
        h_key = str(key).strip()
        h_val = str(val).strip()
        if h_key and h_val:
            headers[h_key] = h_val
    return headers


def _normalize_bearer_value(value: str | None) -> str:
    token = _clean_env(value)
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _resolve_connection_id_by_name(project: AIProjectClient, connection_name: str) -> str:
    resolved_name = _clean_env(connection_name)
    if not resolved_name:
        return ""
    conn = project.connections.get(name=resolved_name)
    conn_id = str(getattr(conn, "id", "") or "").strip()
    if conn_id:
        return conn_id
    return ""


def _auto_resolve_remote_mcp_connection_id(project: AIProjectClient, server_url: str) -> str:
    wanted = server_url.rstrip("/").lower()
    matches: list[tuple[str, bool, str]] = []

    for conn in project.connections.list(connection_type="RemoteTool_Preview"):
        conn_id = str(getattr(conn, "id", "") or "").strip()
        target = str(getattr(conn, "target", "") or "").strip()
        is_default = bool(getattr(conn, "is_default", False))
        if not conn_id:
            continue
        if target.rstrip("/").lower() == wanted:
            matches.append((conn_id, is_default, target))

    if not matches:
        return ""

    # Prefer exact match that is marked default; otherwise first exact match.
    matches.sort(key=lambda item: (not item[1], item[2]))
    return matches[0][0]


def _resolve_mcp_project_connection_id(project: AIProjectClient, server_url: str) -> str:
    explicit_id_or_name = _clean_env(os.getenv("FOUNDRY_MCP_PROJECT_CONNECTION_ID"))
    explicit_name = _clean_env(os.getenv("FOUNDRY_MCP_PROJECT_CONNECTION_NAME"))

    if explicit_id_or_name:
        if "/" in explicit_id_or_name:
            logger.info("[Build] Using explicit MCP project_connection_id from env.")
            return explicit_id_or_name
        try:
            resolved = _resolve_connection_id_by_name(project, explicit_id_or_name)
            if resolved:
                logger.info(
                    "[Build] Resolved FOUNDRY_MCP_PROJECT_CONNECTION_ID as connection name -> id."
                )
                return resolved
        except Exception as exc:
            logger.warning(
                "[Build] Failed to resolve FOUNDRY_MCP_PROJECT_CONNECTION_ID as name (%s): %s",
                explicit_id_or_name,
                exc,
            )
        logger.info("[Build] Using non-resource MCP connection identifier directly.")
        return explicit_id_or_name

    if explicit_name:
        try:
            resolved = _resolve_connection_id_by_name(project, explicit_name)
            if resolved:
                logger.info("[Build] Resolved FOUNDRY_MCP_PROJECT_CONNECTION_NAME -> id.")
                return resolved
        except Exception as exc:
            logger.warning(
                "[Build] Failed to resolve FOUNDRY_MCP_PROJECT_CONNECTION_NAME (%s): %s",
                explicit_name,
                exc,
            )

    auto_resolve = _parse_bool(
        os.getenv("FOUNDRY_MCP_PROJECT_CONNECTION_AUTO_RESOLVE"),
        default=True,
    )
    if not auto_resolve:
        return ""

    try:
        resolved = _auto_resolve_remote_mcp_connection_id(project, server_url)
        if resolved:
            logger.info("[Build] Auto-resolved RemoteTool_Preview connection for MCP server.")
            return resolved
    except Exception as exc:
        logger.warning("[Build] Auto-resolve MCP connection failed: %s", exc)

    return ""


def _build_mcp_authorization() -> str:
    explicit_auth = _normalize_bearer_value(os.getenv("FOUNDRY_MCP_AUTHORIZATION"))
    if explicit_auth:
        return explicit_auth

    use_azd = _parse_bool(os.getenv("FOUNDRY_MCP_USE_AZD_TOKEN"), default=False)
    if not use_azd:
        return ""

    scope = _clean_env(os.getenv("FOUNDRY_MCP_AUTH_SCOPE")) or DEFAULT_FABRIC_SCOPE
    tenant_id = _clean_env(os.getenv("FOUNDRY_MCP_AUTH_TENANT_ID"))
    cred = AzureDeveloperCliCredential(
        tenant_id=tenant_id or None,
        process_timeout=30,
    )
    try:
        token = cred.get_token(scope).token
        logger.info("[Build] Generated MCP Bearer token via AzureDeveloperCliCredential.")
        return f"Bearer {token}"
    except (CredentialUnavailableError, ClientAuthenticationError) as exc:
        raise RuntimeError(
            "Failed to acquire MCP authorization token via azd. "
            "Run 'azd auth login' or set FOUNDRY_MCP_PROJECT_CONNECTION_ID."
        ) from exc
    finally:
        try:
            cred.close()
        except Exception:
            pass


def _build_structured_inputs(max_slots: int) -> dict[str, StructuredInputDefinition]:
    inputs: dict[str, StructuredInputDefinition] = {}
    for slot in range(1, max_slots + 1):
        inputs[f"file_id_{slot}"] = StructuredInputDefinition(
            description=f"Runtime uploaded Foundry file ID for slot {slot}.",
            required=False,
            default_value="",
            schema={
                "type": "string",
                "description": f"Foundry file ID for slot {slot}",
            },
        )
    return inputs


def _build_instructions(default_artifact_id: str) -> str:
    lines = [
        "You are an enterprise data analysis assistant.",
        "Always use available tools when data retrieval or calculation is needed.",
        "If user uploaded files are present, use Code Interpreter for parsing/calculation/chart generation.",
        "When Fabric/Power BI MCP tools are available, use them for semantic-model data questions.",
        "For DAX-related requests, call GenerateQuery before ExecuteQuery whenever feasible.",
        "If a question targets a semantic model and artifactId is missing, use the configured default artifactId.",
        "Explain assumptions and keep business answers concise and verifiable.",
    ]
    if default_artifact_id:
        lines.append(f"Configured default artifactId: {default_artifact_id}")
    return "\n".join(lines)


def _build_mcp_tool(project: AIProjectClient) -> MCPTool | None:
    enable_mcp = _parse_bool(os.getenv("FOUNDRY_ENABLE_MCP"), default=True)
    if not enable_mcp:
        logger.info("[Build] MCP tool disabled by FOUNDRY_ENABLE_MCP=false")
        return None

    server_label = _clean_env(os.getenv("FOUNDRY_MCP_SERVER_LABEL")) or "powerbi_fabric_mcp"
    server_description = (
        _clean_env(os.getenv("FOUNDRY_MCP_SERVER_DESCRIPTION"))
        or "Power BI / Fabric Remote MCP server"
    )

    server_url = (
        _clean_env(os.getenv("FOUNDRY_MCP_SERVER_URL"))
        or _clean_env(os.getenv("FABRIC_POWERBI_MCP_URL"))
        or DEFAULT_FABRIC_MCP_URL
    )
    server_url = _must_http_url("FOUNDRY_MCP_SERVER_URL", server_url)

    project_connection_id = _resolve_mcp_project_connection_id(project, server_url)
    authorization = _build_mcp_authorization()
    headers = _parse_json_headers(os.getenv("FOUNDRY_MCP_HEADERS_JSON"))
    allowed_tools = _parse_csv(os.getenv("FOUNDRY_MCP_ALLOWED_TOOLS"))
    require_approval = _clean_env(os.getenv("FOUNDRY_MCP_REQUIRE_APPROVAL")).lower()
    fail_if_unauth = _parse_bool(os.getenv("FOUNDRY_MCP_FAIL_IF_UNAUTH"), default=True)

    # For compatibility, also emit Authorization header when we have a bearer token.
    if authorization and "Authorization" not in headers and "authorization" not in headers:
        headers["Authorization"] = authorization

    mcp_kwargs: dict[str, Any] = {
        "server_label": server_label,
        "server_url": server_url,
        "server_description": server_description,
    }
    if project_connection_id:
        mcp_kwargs["project_connection_id"] = project_connection_id
        if authorization:
            logger.info(
                "[Build] MCP project_connection_id is set; ignore explicit authorization token."
            )
    elif authorization:
        mcp_kwargs["authorization"] = authorization
    if headers:
        mcp_kwargs["headers"] = headers
    if allowed_tools:
        mcp_kwargs["allowed_tools"] = allowed_tools
    if require_approval:
        if require_approval not in {"always", "never"}:
            raise RuntimeError("FOUNDRY_MCP_REQUIRE_APPROVAL must be one of: always, never")
        mcp_kwargs["require_approval"] = require_approval

    has_auth_header = bool(headers.get("Authorization") or headers.get("authorization"))
    has_any_auth = bool(project_connection_id or authorization or has_auth_header)
    is_fabric_mcp = "api.fabric.microsoft.com" in server_url.lower()
    if not has_any_auth:
        message = (
            "MCP tool has no auth config (no project_connection_id / authorization / Authorization header). "
            "This will likely fail with 401 TokenIsMissing."
        )
        if fail_if_unauth or is_fabric_mcp:
            raise RuntimeError(message)
        logger.warning("[Build] %s", message)

    logger.info(
        "[Build] MCP tool config | label=%s | url=%s | conn_id=%s | has_auth=%s | allowed_tools=%s",
        server_label,
        server_url,
        project_connection_id or "<none>",
        has_any_auth,
        allowed_tools,
    )
    return MCPTool(**mcp_kwargs)


def _build_tools(project: AIProjectClient, max_file_slots: int) -> list[Any]:
    file_placeholders = [f"{{{{file_id_{i}}}}}" for i in range(1, max_file_slots + 1)]

    tools: list[Any] = [
        CodeInterpreterTool(
            container=AutoCodeInterpreterToolParam(file_ids=file_placeholders),
        )
    ]

    mcp_tool = _build_mcp_tool(project)
    if mcp_tool is not None:
        tools.append(mcp_tool)

    logger.info("[Build] tools=%s", [type(tool).__name__ for tool in tools])
    return tools


def _read_file_slot_count() -> int:
    raw = _clean_env(os.getenv("FOUNDRY_FILE_SLOT_COUNT"))
    if not raw:
        return DEFAULT_FILE_SLOTS
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("FOUNDRY_FILE_SLOT_COUNT must be an integer.") from exc
    if value <= 0:
        raise RuntimeError("FOUNDRY_FILE_SLOT_COUNT must be > 0.")
    return value


def main() -> None:
    endpoint = _clean_env(os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")) or _clean_env(
        os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    )
    endpoint = _must_http_url("AZURE_AI_PROJECT_ENDPOINT", endpoint)

    model_name = _clean_env(os.getenv("FOUNDRY_MODEL_DEPLOYMENT")) or DEFAULT_MODEL
    agent_name = _clean_env(os.getenv("FOUNDRY_AGENT_NAME")) or DEFAULT_AGENT_NAME
    max_file_slots = _read_file_slot_count()

    default_artifact_id = (
        _clean_env(os.getenv("FABRIC_ARTIFACT_ID"))
        or _clean_env(os.getenv("FABRIC_SEMANTIC_MODEL_ID"))
    )

    logger.info("[Config] endpoint=%s", endpoint)
    logger.info("[Config] model_name=%s", model_name)
    logger.info("[Config] agent_name=%s", agent_name)
    logger.info("[Config] max_file_slots=%d", max_file_slots)
    logger.info("[Config] default_artifact_id=%s", default_artifact_id or "<empty>")

    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=endpoint, credential=credential)

    try:
        structured_inputs = _build_structured_inputs(max_file_slots)
        tools = _build_tools(project, max_file_slots)
        instructions = _build_instructions(default_artifact_id)

        definition = PromptAgentDefinition(
            model=model_name,
            instructions=instructions,
            tools=tools,
            structured_inputs=structured_inputs,
        )

        logger.info("[Build] creating agent version...")
        agent = project.agents.create_version(
            agent_name=agent_name,
            definition=definition,
        )

        logger.info("[Done] agent version created successfully")
        logger.info("[Done] agent_name=%s", agent.name)
        logger.info("[Done] agent_version=%s", agent.version)
        logger.info("[Done] agent_id=%s", getattr(agent, "id", ""))

        print("Agent version created successfully")
        print(f"agent_name={agent.name}")
        print(f"agent_version={agent.version}")
        print(f"agent_id={getattr(agent, 'id', '')}")

    except Exception as exc:
        logger.error("[Error] create/update agent failed: %s", exc)
        logger.debug(traceback.format_exc())
        raise

    finally:
        try:
            project.close()
        except Exception:
            pass
        try:
            credential.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
