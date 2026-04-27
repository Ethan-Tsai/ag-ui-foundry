from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import httpx
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import (
        AzureDeveloperCliCredential,
        CredentialUnavailableError,
        DefaultAzureCredential,
    )
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
except ImportError as exc:
    missing = str(exc)
    raise SystemExit(
        "Missing dependency for pbi_fabric_mcp_client.py. "
        "Please install required packages (mcp, azure-identity, httpx). "
        f"Original error: {missing}"
    ) from exc

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:  # type: ignore[no-redef]
        return

load_dotenv()

DEFAULT_MCP_URL = "https://api.fabric.microsoft.com/v1/mcp/powerbi"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
DEFAULT_TEST_ARTIFACT_ID = "c815797b-8708-44d5-9f02-1e11f324b690"
DEFAULT_TEST_USER_INPUT = (
    "請給我 U01200 在 2022 年的 BU standard performance、revenue 與 revenue share"
)
DEFAULT_EXECUTE_TEST_DAX = "EVALUATE TOPN(100, 'Rev% Group')"
_TOKEN_CACHE: dict[str, tuple[str, int]] = {}
_TOKEN_CACHE_LOCK = threading.Lock()


@dataclass
class ClientConfig:
    mcp_url: str
    scope: str
    tenant_id: str | None
    auth_mode: str
    timeout_seconds: float
    artifact_id: str | None
    semantic_model_id: str | None
    verbose: bool


@dataclass
class ToolCallOutput:
    tool_name: str
    arguments: dict[str, Any]
    output_text: str
    structured_content: Any
    is_error: bool
    raw: Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Power BI Fabric MCP direct client (azd-auth-first).",
    )
    parser.add_argument(
        "--mcp-url",
        default=os.getenv("FABRIC_POWERBI_MCP_URL", DEFAULT_MCP_URL),
        help="Remote MCP endpoint URL.",
    )
    parser.add_argument(
        "--scope",
        default=os.getenv("FABRIC_POWERBI_MCP_SCOPE", FABRIC_SCOPE),
        help="AAD scope used to request the Fabric access token.",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("MCP_TENANT_ID") or os.getenv("AZURE_TENANT_ID"),
        help="Optional tenant override.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["azd", "default"],
        default=os.getenv("MCP_AUTH_MODE", "azd"),
        help="azd: force AzureDeveloperCliCredential (recommended for this script).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("MCP_HTTP_TIMEOUT_SECONDS", "45")),
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--artifact-id",
        default=(
            os.getenv("FABRIC_ARTIFACT_ID", "").strip()
            or os.getenv("FABRIC_SEMANTIC_MODEL_ID", "").strip()
            or None
        ),
        help="Default artifact id for Power BI MCP tools.",
    )
    parser.add_argument(
        "--semantic-model-id",
        default=os.getenv("FABRIC_SEMANTIC_MODEL_ID", "").strip() or None,
        help="Optional semantic model id alias. If artifactId is missing, this value is reused.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed schemas/responses for troubleshooting.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="Validate token + MCP handshake.")
    subparsers.add_parser("token-info", help="Print access token claims (tid/upn/appid/exp).")
    subparsers.add_parser("tools", help="List available MCP tools.")
    subparsers.add_parser("diag", help="Print endpoint/auth summary and tool schemas (with --verbose).")
    subparsers.add_parser("resources", help="List available MCP resources.")
    subparsers.add_parser("prompts", help="List available MCP prompts.")
    subparsers.add_parser("generate-test", help="Run GenerateQuery with built-in test payload (no --args needed).")
    subparsers.add_parser("dax-test", help="Run GenerateQuery + ExecuteQuery end-to-end with built-in payload.")
    subparsers.add_parser("execute-test", help="Run ExecuteQuery with built-in DAX payload (no --args needed).")

    call_parser = subparsers.add_parser("call", help="Call one MCP tool.")
    call_parser.add_argument("--name", required=True, help="Tool name")
    call_parser.add_argument(
        "--args",
        default="{}",
        help="Tool arguments JSON string, e.g. '{\"semanticModelId\":\"...\"}'.",
    )

    return parser.parse_args()


def _build_config(args: argparse.Namespace) -> ClientConfig:
    return ClientConfig(
        mcp_url=args.mcp_url,
        scope=args.scope,
        tenant_id=args.tenant_id,
        auth_mode=args.auth_mode,
        timeout_seconds=args.timeout,
        artifact_id=args.artifact_id,
        semantic_model_id=args.semantic_model_id,
        verbose=args.verbose,
    )


def build_config_from_env(*, verbose: bool = False) -> ClientConfig:
    return ClientConfig(
        mcp_url=os.getenv("FABRIC_POWERBI_MCP_URL", DEFAULT_MCP_URL),
        scope=os.getenv("FABRIC_POWERBI_MCP_SCOPE", FABRIC_SCOPE),
        tenant_id=os.getenv("MCP_TENANT_ID") or os.getenv("AZURE_TENANT_ID"),
        auth_mode=os.getenv("MCP_AUTH_MODE", "azd"),
        timeout_seconds=float(os.getenv("MCP_HTTP_TIMEOUT_SECONDS", "45")),
        artifact_id=(
            os.getenv("FABRIC_ARTIFACT_ID", "").strip()
            or os.getenv("FABRIC_SEMANTIC_MODEL_ID", "").strip()
            or None
        ),
        semantic_model_id=os.getenv("FABRIC_SEMANTIC_MODEL_ID", "").strip() or None,
        verbose=verbose,
    )


def _build_credential(config: ClientConfig):
    tenant_id = config.tenant_id or ""
    if config.auth_mode == "azd":
        return AzureDeveloperCliCredential(tenant_id=tenant_id, process_timeout=20)

    return DefaultAzureCredential(
        exclude_interactive_browser_credential=False,
        exclude_azure_developer_cli_credential=False,
    )


def _token_cache_key(config: ClientConfig) -> str:
    return f"{config.auth_mode}|{config.tenant_id or ''}|{config.scope}"


def _fetch_access_token(config: ClientConfig) -> str:
    key = _token_cache_key(config)
    now = int(time.time())
    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > now + 60:
        return cached[0]

    credential = _build_credential(config)
    try:
        token = credential.get_token(config.scope)
        expires_on = int(getattr(token, "expires_on", 0) or 0)
        if expires_on <= 0:
            claims = _decode_jwt_payload(token.token)
            expires_on = int(claims.get("exp") or (now + 300))
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE[key] = (token.token, expires_on)
        return token.token
    except (CredentialUnavailableError, ClientAuthenticationError) as exc:
        raise RuntimeError(
            "Failed to acquire Fabric access token. Run 'azd auth login' first, "
            "then retry."
        ) from exc
    finally:
        try:
            credential.close()
        except Exception:
            pass


def _decode_tool_args(raw_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for --args: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("--args must be a JSON object.")
    return parsed


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    value = (text or "").strip()
    if not value:
        return None

    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        snippet = value[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def _extract_dax_query_from_result(result: Any) -> str:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        for key in ("daxQuery", "query", "dax"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    content_list = getattr(result, "content", None) or []
    for item in content_list:
        text_value = getattr(item, "text", None)
        if not text_value:
            continue
        payload = _extract_json_object_from_text(str(text_value))
        if isinstance(payload, dict):
            for key in ("daxQuery", "query", "dax"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        fenced = re.findall(r"```(?:dax)?\s*([\s\S]*?)```", str(text_value), flags=re.IGNORECASE)
        for block in fenced:
            if "EVALUATE" in block.upper():
                return block.strip()

        match = re.search(r"(EVALUATE[\s\S]+)$", str(text_value), flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return ""


def _build_generate_test_args(config: ClientConfig) -> dict[str, Any]:
    artifact_id = (
        os.getenv("PBI_TEST_ARTIFACT_ID", "").strip()
        or DEFAULT_TEST_ARTIFACT_ID
        or config.artifact_id
        or config.semantic_model_id
        or ""
    )
    if not artifact_id:
        raise ValueError(
            "Missing artifactId for test run. Set FABRIC_ARTIFACT_ID or PBI_TEST_ARTIFACT_ID in .env."
        )

    user_input = os.getenv("PBI_TEST_USER_INPUT", "").strip() or DEFAULT_TEST_USER_INPUT
    return {
        "artifactId": artifact_id,
        "userInput": user_input,
    }


def _build_execute_test_args(config: ClientConfig) -> dict[str, Any]:
    artifact_id = (
        os.getenv("PBI_TEST_ARTIFACT_ID", "").strip()
        or DEFAULT_TEST_ARTIFACT_ID
        or config.artifact_id
        or config.semantic_model_id
        or ""
    )
    if not artifact_id:
        raise ValueError(
            "Missing artifactId for test run. Set FABRIC_ARTIFACT_ID or PBI_TEST_ARTIFACT_ID in .env."
        )

    dax_query = os.getenv("PBI_TEST_EXECUTE_DAX", "").strip() or DEFAULT_EXECUTE_TEST_DAX
    max_rows = int(os.getenv("PBI_TEST_MAX_ROWS", "250"))
    return {
        "artifactId": artifact_id,
        "daxQuery": dax_query,
        "maxRows": max_rows,
    }


def _apply_default_semantic_model_id(
    tool_args: dict[str, Any],
    config: ClientConfig,
) -> dict[str, Any]:
    updated = dict(tool_args)
    default_artifact_id = config.artifact_id or config.semantic_model_id

    # Power BI remote MCP tools require artifactId.
    if not updated.get("artifactId") and default_artifact_id:
        updated["artifactId"] = default_artifact_id

    # Keep semanticModelId as a compatibility alias for client-side workflows.
    if not updated.get("semanticModelId") and config.semantic_model_id:
        updated["semanticModelId"] = config.semantic_model_id

    return updated


def _validate_required_powerbi_args(
    tool_name: str,
    tool_args: dict[str, Any],
) -> None:
    if tool_name in {"GetSemanticModelSchema", "GenerateQuery", "ExecuteQuery"}:
        if not tool_args.get("artifactId"):
            raise ValueError(
                "Missing required argument 'artifactId'. "
                "Set FABRIC_ARTIFACT_ID in .env or pass --args with artifactId."
            )


def _render_tool_result(result: Any) -> str:
    lines: list[str] = []

    content_list = getattr(result, "content", None) or []
    if content_list:
        for index, item in enumerate(content_list, start=1):
            text_value = getattr(item, "text", None)
            if text_value:
                lines.append(f"[content:{index}] {text_value}")
                continue

            if hasattr(item, "model_dump"):
                lines.append(f"[content:{index}] {json.dumps(item.model_dump(), ensure_ascii=False)}")
            else:
                lines.append(f"[content:{index}] {item}")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        lines.append("[structured]")
        lines.append(json.dumps(structured, ensure_ascii=False, indent=2))

    if not lines:
        if hasattr(result, "model_dump"):
            return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
        return str(result)

    return "\n".join(lines)


def _safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)


def _decode_jwt_payload(access_token: str) -> dict[str, Any]:
    parts = access_token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    padding = "=" * ((4 - len(payload_b64) % 4) % 4)
    payload_b64 += padding
    try:
        decoded = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        parsed = json.loads(decoded.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    config: ClientConfig | None = None,
) -> ToolCallOutput:
    cfg = config or build_config_from_env()
    access_token = _fetch_access_token(cfg)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(cfg.timeout_seconds, read=cfg.timeout_seconds)

    tool_args = _apply_default_semantic_model_id(arguments, cfg)
    _validate_required_powerbi_args(tool_name, tool_args)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(cfg.mcp_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=tool_args)
                rendered = _render_tool_result(result)
                is_error = bool(getattr(result, "isError", False))
                structured = getattr(result, "structuredContent", None)
                raw_payload = result.model_dump() if hasattr(result, "model_dump") else result
                return ToolCallOutput(
                    tool_name=tool_name,
                    arguments=tool_args,
                    output_text=rendered,
                    structured_content=structured,
                    is_error=is_error,
                    raw=raw_payload,
                )


def call_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    config: ClientConfig | None = None,
) -> ToolCallOutput:
    return asyncio.run(call_tool(tool_name, arguments, config=config))


async def list_tools(*, config: ClientConfig | None = None) -> list[dict[str, Any]]:
    cfg = config or build_config_from_env()
    access_token = _fetch_access_token(cfg)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(cfg.timeout_seconds, read=cfg.timeout_seconds)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(cfg.mcp_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                return [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": getattr(t, "inputSchema", None),
                    }
                    for t in tools.tools
                ]


def list_tools_sync(*, config: ClientConfig | None = None) -> list[dict[str, Any]]:
    return asyncio.run(list_tools(config=config))


async def run_tool_calls(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    config: ClientConfig | None = None,
) -> list[ToolCallOutput]:
    cfg = config or build_config_from_env()
    access_token = _fetch_access_token(cfg)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(cfg.timeout_seconds, read=cfg.timeout_seconds)
    outputs: list[ToolCallOutput] = []

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(cfg.mcp_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for tool_name, args in calls:
                    tool_args = _apply_default_semantic_model_id(args, cfg)
                    _validate_required_powerbi_args(tool_name, tool_args)
                    result = await session.call_tool(tool_name, arguments=tool_args)
                    rendered = _render_tool_result(result)
                    is_error = bool(getattr(result, "isError", False))
                    structured = getattr(result, "structuredContent", None)
                    raw_payload = result.model_dump() if hasattr(result, "model_dump") else result
                    outputs.append(
                        ToolCallOutput(
                            tool_name=tool_name,
                            arguments=tool_args,
                            output_text=rendered,
                            structured_content=structured,
                            is_error=is_error,
                            raw=raw_payload,
                        )
                    )
    return outputs


def run_tool_calls_sync(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    config: ClientConfig | None = None,
) -> list[ToolCallOutput]:
    return asyncio.run(run_tool_calls(calls, config=config))


async def _run_command(config: ClientConfig, args: argparse.Namespace) -> None:
    access_token = _fetch_access_token(config)
    token_claims = _decode_jwt_payload(access_token)

    if args.command == "token-info":
        selected = {
            "tid": token_claims.get("tid"),
            "oid": token_claims.get("oid"),
            "upn": token_claims.get("upn"),
            "preferred_username": token_claims.get("preferred_username"),
            "appid": token_claims.get("appid"),
            "aud": token_claims.get("aud"),
            "iss": token_claims.get("iss"),
            "exp": token_claims.get("exp"),
        }
        print(_safe_json_dumps(selected))
        return

    prepared_call_name: str | None = None
    prepared_call_args: dict[str, Any] | None = None

    if args.command == "call":
        tool_args = _decode_tool_args(args.args)
        tool_args = _apply_default_semantic_model_id(tool_args, config)
        _validate_required_powerbi_args(args.name, tool_args)
        prepared_call_name = args.name
        prepared_call_args = tool_args

    if args.command in {"generate-test", "dax-test"}:
        tool_args = _build_generate_test_args(config)
        tool_args = _apply_default_semantic_model_id(tool_args, config)
        _validate_required_powerbi_args("GenerateQuery", tool_args)
        prepared_call_name = "GenerateQuery"
        prepared_call_args = tool_args

    if args.command == "execute-test":
        tool_args = _build_execute_test_args(config)
        tool_args = _apply_default_semantic_model_id(tool_args, config)
        _validate_required_powerbi_args("ExecuteQuery", tool_args)
        prepared_call_name = "ExecuteQuery"
        prepared_call_args = tool_args

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(config.timeout_seconds, read=config.timeout_seconds)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(config.mcp_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                if args.command == "check":
                    await session.send_ping()
                    tools = await session.list_tools()
                    print(f"Connected to: {config.mcp_url}")
                    print(f"Tool count: {len(tools.tools)}")
                    return

                if args.command == "tools":
                    tools = await session.list_tools()
                    for tool in tools.tools:
                        print(f"- {tool.name}: {tool.description or ''}")
                        if config.verbose:
                            schema = getattr(tool, "inputSchema", None)
                            if schema is not None:
                                print(f"  inputSchema: {_safe_json_dumps(schema)}")
                    return

                if args.command == "diag":
                    await session.send_ping()
                    tools = await session.list_tools()
                    print("MCP diagnostic:")
                    print(f"- endpoint: {config.mcp_url}")
                    print(f"- auth_mode: {config.auth_mode}")
                    print(f"- scope: {config.scope}")
                    print(f"- artifact_id(default): {config.artifact_id or config.semantic_model_id or '<empty>'}")
                    print(f"- tool_count: {len(tools.tools)}")
                    print("")
                    for tool in tools.tools:
                        print(f"- {tool.name}: {tool.description or ''}")
                        if config.verbose:
                            schema = getattr(tool, "inputSchema", None)
                            if schema is not None:
                                print(f"  inputSchema: {_safe_json_dumps(schema)}")
                    return

                if args.command == "resources":
                    resources = await session.list_resources()
                    for resource in resources.resources:
                        print(f"- {resource.uri}")
                    return

                if args.command == "prompts":
                    prompts = await session.list_prompts()
                    for prompt in prompts.prompts:
                        print(f"- {prompt.name}: {prompt.description or ''}")
                    return

                if args.command == "call":
                    assert prepared_call_name is not None and prepared_call_args is not None
                    result = await session.call_tool(prepared_call_name, arguments=prepared_call_args)
                    print(_render_tool_result(result))
                    is_error = bool(getattr(result, "isError", False))
                    if is_error or config.verbose:
                        print("\n--- raw CallToolResult ---")
                        if hasattr(result, "model_dump"):
                            print(_safe_json_dumps(result.model_dump()))
                        else:
                            print(str(result))
                    return

                if args.command == "generate-test":
                    assert prepared_call_args is not None
                    print("Running GenerateQuery test:")
                    print(f"- endpoint: {config.mcp_url}")
                    print(f"- artifactId: {prepared_call_args.get('artifactId')}")
                    print(f"- userInput: {prepared_call_args.get('userInput')}")
                    result = await session.call_tool("GenerateQuery", arguments=prepared_call_args)
                    print(_render_tool_result(result))
                    is_error = bool(getattr(result, "isError", False))
                    if is_error or config.verbose:
                        print("\n--- raw CallToolResult ---")
                        if hasattr(result, "model_dump"):
                            print(_safe_json_dumps(result.model_dump()))
                        else:
                            print(str(result))
                    return

                if args.command == "dax-test":
                    assert prepared_call_args is not None
                    print("Running DAX end-to-end test:")
                    print(f"- endpoint: {config.mcp_url}")
                    print(f"- artifactId: {prepared_call_args.get('artifactId')}")
                    print(f"- userInput: {prepared_call_args.get('userInput')}")

                    generate_result = await session.call_tool("GenerateQuery", arguments=prepared_call_args)
                    print("\n[GenerateQuery]")
                    print(_render_tool_result(generate_result))
                    generate_error = bool(getattr(generate_result, "isError", False))
                    if generate_error:
                        print("\nGenerateQuery returned isError=true, stop test.")
                        if hasattr(generate_result, "model_dump"):
                            print(_safe_json_dumps(generate_result.model_dump()))
                        return

                    dax_query = _extract_dax_query_from_result(generate_result)
                    if not dax_query:
                        print("\nCould not extract DAX from GenerateQuery result.")
                        if hasattr(generate_result, "model_dump"):
                            print(_safe_json_dumps(generate_result.model_dump()))
                        return

                    execute_args = {
                        "artifactId": prepared_call_args.get("artifactId"),
                        "daxQuery": dax_query,
                        "maxRows": int(os.getenv("PBI_TEST_MAX_ROWS", "250")),
                    }
                    print("\n[ExecuteQuery args]")
                    print(_safe_json_dumps(execute_args))

                    execute_result = await session.call_tool("ExecuteQuery", arguments=execute_args)
                    print("\n[ExecuteQuery]")
                    print(_render_tool_result(execute_result))
                    execute_error = bool(getattr(execute_result, "isError", False))
                    if execute_error or config.verbose:
                        print("\n--- raw ExecuteQuery CallToolResult ---")
                        if hasattr(execute_result, "model_dump"):
                            print(_safe_json_dumps(execute_result.model_dump()))
                        else:
                            print(str(execute_result))
                    return

                if args.command == "execute-test":
                    assert prepared_call_args is not None
                    print("Running ExecuteQuery test:")
                    print(f"- endpoint: {config.mcp_url}")
                    print(f"- artifactId: {prepared_call_args.get('artifactId')}")
                    print(f"- daxQuery: {prepared_call_args.get('daxQuery')}")
                    print(f"- maxRows: {prepared_call_args.get('maxRows')}")

                    result = await session.call_tool("ExecuteQuery", arguments=prepared_call_args)
                    print(_render_tool_result(result))
                    is_error = bool(getattr(result, "isError", False))
                    if is_error or config.verbose:
                        print("\n--- raw ExecuteQuery CallToolResult ---")
                        if hasattr(result, "model_dump"):
                            print(_safe_json_dumps(result.model_dump()))
                        else:
                            print(str(result))
                    return

                raise RuntimeError(f"Unsupported command: {args.command}")


def main() -> None:
    args = _parse_args()
    config = _build_config(args)
    asyncio.run(_run_command(config, args))


if __name__ == "__main__":
    main()
