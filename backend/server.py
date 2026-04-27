# Copyright (c) Microsoft. All rights reserved.

"""Example FastAPI server with AG-UI endpoints."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from urllib.parse import quote, unquote

# Add project root to sys.path so 'python backend/server.py' works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from agent_framework.ag_ui import AgentFrameworkAgent, add_agent_framework_fastapi_endpoint
from agent_framework.openai import OpenAIChatClient
from agent_framework_foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from backend.local_agent import local_agent
from backend.state import INITIAL_POWERBI_STATE, PREDICT_STATE_CONFIG, STATE_SCHEMA
from backend.dynamic_agent import get_dynamic_router_agent, current_agent_id

load_dotenv()  # Load environment variables from .env file if present

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure predictable backend logging without changing runtime behavior."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        root_logger.setLevel(level)

    if os.getenv("ENABLE_DEBUG_LOGGING"):
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "server.log")

        if not any(
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", "") == os.path.abspath(log_file)
            for handler in root_logger.handlers
        ):
            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        logger.info("Debug file logging enabled path=%s level=%s", log_file, log_level)

    logging.getLogger("agent_framework_ag_ui").setLevel(level)
    logging.getLogger("agent_framework").setLevel(level)


_configure_logging()


def create_app(agent_kind: str | None = None) -> FastAPI:
    app = FastAPI(title="Agent Framework AG-UI Server")
    selected_agent = (agent_kind or os.getenv("AGENT_KIND", "local")).strip().lower()
    cors_origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOW_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]
    allow_credentials = "*" not in cors_origins
    logger.info("Creating FastAPI app agent_kind=%s", selected_agent)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:3000"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def intercept_agent_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:10]
        started_at = time.perf_counter()
        agent_id = request.query_params.get("agentId")
        if agent_id:
            current_agent_id.set(agent_id)

        logger.info(
            "HTTP start request_id=%s method=%s path=%s agentId=%s content_length=%s",
            request_id,
            request.method,
            request.url.path,
            agent_id or "-",
            request.headers.get("content-length", "-"),
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "HTTP failed request_id=%s method=%s path=%s agentId=%s duration_ms=%.1f",
                request_id,
                request.method,
                request.url.path,
                agent_id or "-",
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["x-request-id"] = request_id
        logger.info(
            "HTTP finish request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    if selected_agent == "foundry":
        logger.info("Initializing dynamic Foundry router agent")
        agent = AgentFrameworkAgent(
            agent=get_dynamic_router_agent(),
            name="foundry_router",
            description="Dynamic Foundry router",
            state_schema=STATE_SCHEMA,
            predict_state_config=PREDICT_STATE_CONFIG,
            require_confirmation=False,
        )
    else:
        logger.info("Initializing local agent")
        agent = local_agent(_create_local_client())

    add_agent_framework_fastapi_endpoint(
        app=app,
        agent=agent,
        path="/ag-ui",
        default_state=INITIAL_POWERBI_STATE,
    )

    # Optional root endpoint for health check
    @app.get("/")
    async def root():
        logger.debug("Health root requested agent=%s", selected_agent)
        return {
            "status": "running",
            "message": "AG-UI endpoint available at /ag-ui",
            "agent": selected_agent,
        }

    @app.get("/api/agents")
    async def list_agents():
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        import os
        import asyncio

        endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        if not endpoint:
            logger.error("List agents failed: AZURE_AI_PROJECT_ENDPOINT missing")
            return {"error": "AZURE_AI_PROJECT_ENDPOINT missing"}

        # Try the module-level cache first (populated by background thread)
        from backend.dynamic_agent import _agent_list_cache
        if _agent_list_cache is not None:
            logger.info("List agents served from cache count=%d", len(_agent_list_cache))
            return {"agents": _agent_list_cache}

        def _fetch_agents():
            credential = DefaultAzureCredential()
            try:
                logger.info("List agents fetching from Foundry endpoint=%s", endpoint)
                agents = []
                with AIProjectClient(endpoint=endpoint, credential=credential) as client:
                    for ag in client.agents.list():
                        desc = getattr(ag, "description", "") or ""
                        metadata = getattr(ag, "metadata", {}) or {}
                        if not desc and isinstance(metadata, dict):
                            desc = str(metadata.get("description", "") or "")

                        agents.append({
                            "name": ag.name,
                            "description": desc or "AI Agent"
                        })
                logger.info("List agents fetched count=%d", len(agents))
                return {"agents": agents}
            except Exception as e:
                logger.exception("List agents fetch failed")
                return {"error": str(e), "agents": []}
            finally:
                credential.close()

        # Run in executor to not block the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch_agents)

    @app.get("/api/agent-info")
    async def get_agent_info(agent_name: str = ""):
        if not agent_name:
            logger.warning("Agent info request rejected: missing agent_name")
            return JSONResponse(status_code=400, content={"error": "agent_name is required"})

        from backend.foundry_agent import get_agent_metadata
        from azure.identity import DefaultAzureCredential
        import os
        import asyncio

        endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        agent_version = os.getenv("AZURE_AI_PROJECT_AGENT_VERSION", "").strip() or None

        def _fetch():
            credential = DefaultAzureCredential()
            try:
                return get_agent_metadata(endpoint, credential, agent_name, agent_version)
            finally:
                credential.close()

        loop = asyncio.get_event_loop()
        try:
            logger.info("Agent info fetch start agent_name=%s version=%s", agent_name, agent_version or "-")
            info = await loop.run_in_executor(None, _fetch)
            logger.info("Agent info fetch success agent_name=%s", agent_name)
            return info
        except Exception as e:
            logger.exception("Agent info fetch failed agent_name=%s", agent_name)
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/upload")
    async def upload_files(files: list[UploadFile] = File(...)):
        import asyncio

        from backend.foundry_files import upload_foundry_file

        uploaded: dict[str, str] = {}
        failed: list[dict[str, str]] = []
        loop = asyncio.get_event_loop()
        logger.info("Upload request received file_count=%d", len(files))

        for uploaded_file in files:
            file_name = uploaded_file.filename or "uploaded_file"
            file_bytes = await uploaded_file.read()
            try:
                logger.info("Upload file start filename=%s size_bytes=%d", file_name, len(file_bytes))
                file_id = await loop.run_in_executor(
                    None,
                    lambda fb=file_bytes, fn=file_name: upload_foundry_file(fb, fn),
                )
                uploaded[file_name] = file_id
                logger.info("Upload file success filename=%s file_id=%s", file_name, file_id)
            except Exception as exc:
                logger.exception("Upload file failed filename=%s", file_name)
                failed.append({"filename": file_name, "error": str(exc)})

        logger.info("Upload request complete uploaded=%d failed=%d", len(uploaded), len(failed))
        return {"uploaded": uploaded, "failed": failed}

    @app.get("/api/download")
    async def download_file(
        file_id: str,
        container_id: str | None = None,
        filename: str = "output_file",
    ):
        import asyncio

        from backend.foundry_files import get_foundry_file_content

        decoded_name = unquote(filename) or "output_file"
        loop = asyncio.get_event_loop()
        logger.info(
            "Download request start filename=%s file_id=%s container_id=%s",
            decoded_name,
            file_id,
            container_id or "-",
        )
        try:
            content = await loop.run_in_executor(
                None,
                lambda: get_foundry_file_content(file_id, container_id),
            )
        except Exception:
            logger.exception("Download request failed filename=%s file_id=%s", decoded_name, file_id)
            return JSONResponse(status_code=500, content={"error": "File download failed"})
        if not content:
            logger.warning("Download request not found filename=%s file_id=%s", decoded_name, file_id)
            return JSONResponse(status_code=404, content={"error": "File not found"})

        encoded_name = quote(decoded_name, safe="")
        logger.info("Download request success filename=%s size_bytes=%d", decoded_name, len(content))
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            },
        )

    return app


def _create_local_client():
    responses_deployment = os.getenv(
        "AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME", ""
    ).strip()
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()

    if responses_deployment and project_endpoint:
        credential = DefaultAzureCredential()
        return FoundryChatClient(
            project_endpoint=project_endpoint,
            model=responses_deployment,
            credential=credential,
        )

    return OpenAIChatClient()


app = create_app()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AG-UI server.")
    parser.add_argument(
        "--agent",
        choices=("local", "foundry"),
        help="Select which agent backend to use.",
    )
    return parser.parse_args()


def main():
    """Run the server."""
    args = _parse_args()
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    print(f"\nAG-UI Server starting on http://{host}:{port}")
    print("Set ENABLE_DEBUG_LOGGING=1 for detailed request logging\n")

    # Use log_config=None to prevent uvicorn from reconfiguring logging
    # This preserves our file + console logging setup
    uvicorn.run(
        create_app(args.agent),
        host=host,
        port=port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
