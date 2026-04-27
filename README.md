# WITS Agent Atelier

WITS Agent Atelier is an internal enterprise AI Agent Portal that connects a Next.js / CopilotKit frontend to a FastAPI AG-UI backend powered by Azure AI Foundry agents and Microsoft Agent Framework.

The portal is designed for multi-agent discovery, agent-specific chat, runtime file upload, Code Interpreter generated file download, and optional Power BI / Fabric MCP integration.

## Current Capabilities

- Agent catalog: discover Azure AI Foundry agents and select the current agent for the conversation.
- AG-UI chat: stream messages through CopilotKit to the FastAPI AG-UI endpoint.
- Dynamic routing: pass `agentId` from the frontend to the backend and route each run to the selected Foundry agent.
- Runtime file upload: upload files from the frontend and attach Foundry file IDs to the next agent run.
- Generated file download: convert Code Interpreter hosted files into stable `/api/download` links.
- Run context panel: show attached files, upload status, and suggested prompts.
- Enterprise UI: WITS color palette, agent sidebar, current-agent header, chatroom layout, avatar identity, and responsive behavior.
- Observability: backend request logging, upload/download logging, Foundry routing logs, and optional file logging.

## Architecture

```text
Browser / Next.js frontend (localhost:3000)
  |
  |  /api/copilotkit/[integrationId]
  |  /api/upload
  |  /api/download
  |  /api/agents
  |  /api/agent-info
  v
Next.js API proxy
  |
  |  AG_UI_ENDPOINT
  v
FastAPI backend (localhost:8000)
  |
  |  /ag-ui
  v
Microsoft Agent Framework AG-UI adapter
  |
  v
DynamicRouterAgent
  |
  |-- Azure AI Foundry Agent
  |-- FileAwareFoundryChatClient
  |-- structured_inputs for uploaded file IDs
  |-- Code Interpreter output link transform
  v
Azure AI Foundry project / agents / tools
```

## Repository Structure

```text
.
├── backend/                         FastAPI backend and Agent Framework integration
│   ├── server.py                    API server, CORS, AG-UI endpoint, upload/download routes
│   ├── dynamic_agent.py             Dynamic Foundry agent router by selected agentId
│   ├── foundry_runtime.py           File-aware Foundry runtime and generated download links
│   ├── foundry_files.py             Foundry file upload/download helpers
│   ├── foundry_agent.py             Foundry agent metadata and shared tool wiring
│   ├── state.py                     AG-UI shared state schema and tool state mapping
│   └── requirements.txt             Python runtime dependencies
├── frontend/                        Next.js frontend
│   ├── app/page.tsx                 WITS Agent Atelier UI and CopilotKit integration
│   ├── app/style.css                Enterprise UI styling and chatroom layout
│   ├── app/api/                     Frontend API proxy routes
│   ├── public/favicon.ico           Agent avatar and browser favicon
│   └── Dockerfile                   Frontend container build
├── PBI/                             Optional Power BI / Fabric MCP helper utilities
├── create_or_update_agent_v3.py     Optional Foundry agent creation/update script
├── docker-compose.yml               Backend + frontend compose entrypoint
├── docker-compose.frontend.yml      Frontend-only compose entrypoint
├── env.sample                       Safe environment variable template
└── walkthrough.md                   Project walkthrough and implementation notes
```

## Prerequisites

- Python 3.11 or later
- Node.js 20 or Docker Desktop
- Azure CLI or Azure Developer CLI authenticated to the correct tenant
- Azure AI Foundry project with at least one published agent
- Required RBAC permissions for the Foundry project and related Azure resources

## Environment Setup

Copy the sample file and fill in local values:

```powershell
Copy-Item env.sample .env
```

Required backend variables:

| Variable | Purpose | Secret |
| --- | --- | --- |
| `AGENT_KIND` | `foundry` or `local` backend mode | No |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint | No, but environment-specific |
| `AZURE_AI_PROJECT_AGENT_NAME` | Default Foundry agent/router name | No |
| `AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME` | Model deployment for local mode/fallback client | No |
| `AG_UI_ENDPOINT` | Frontend proxy target for backend AG-UI endpoint | No |

Sensitive optional variables:

| Variable | Notes |
| --- | --- |
| `AZURE_OPENAI_API_KEY` | Legacy API-key auth only. Prefer Entra ID auth. Never commit a real key. |
| `FOUNDRY_MCP_AUTHORIZATION` | Bearer token for MCP testing only. Never commit. |
| `FOUNDRY_MCP_HEADERS_JSON` | May contain auth headers. Never commit real values. |

Operational variables:

| Variable | Default | Notes |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Backend bind host |
| `PORT` | `8000` | Backend port |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated allowed browser origins |
| `FOUNDRY_FILE_SLOT_COUNT` | `5` | Number of uploaded file IDs mapped into `structured_inputs` |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` only for local troubleshooting |
| `ENABLE_DEBUG_LOGGING` | unset | When set, writes backend logs to `backend/log/server.log` |

## Run Locally

Install backend dependencies:

```powershell
pip install -r backend/requirements.txt
```

Start backend:

```powershell
python backend/server.py --agent foundry
```

Install frontend dependencies:

```powershell
npm --prefix frontend install
```

Start frontend:

```powershell
npm --prefix frontend run dev
```

Open:

```text
http://localhost:3000
```

## Run With Docker

Frontend only:

```powershell
docker-compose -f docker-compose.frontend.yml up -d --build
```

Full stack, if backend env is configured:

```powershell
docker-compose up -d --build
```

The frontend container uses:

```text
AG_UI_ENDPOINT=http://host.docker.internal:8000/ag-ui
```

This allows the frontend container to call a backend running on the host machine.

## File Upload and Generated Files

Runtime upload flow:

1. Frontend posts files to `/api/upload`.
2. Next.js forwards the request to the backend.
3. Backend uploads files to Azure AI Foundry file storage.
4. Returned Foundry file IDs are attached to the CopilotKit runtime URL as `fileIds`.
5. `FileAwareFoundryChatClient` maps file IDs into `structured_inputs`.

Generated file flow:

1. Foundry / Code Interpreter emits hosted file annotations or outputs.
2. `attach_download_links_transform()` detects generated files.
3. Backend appends `/api/download` links to the assistant response.
4. Frontend renders those links as generated file cards.

## Logging and Debugging

Default logging writes to console. To enable file logging locally:

```powershell
$env:ENABLE_DEBUG_LOGGING = "1"
$env:LOG_LEVEL = "DEBUG"
python backend/server.py --agent foundry
```

Logs include request id, method/path, agentId, status, duration, upload/download status, Foundry routing, file attachment count, and generated file detection. Logs intentionally avoid writing raw file bytes and should not include secrets.

## Security Notes

- `.env` and `.env.*` are ignored by git.
- Do not commit API keys, bearer tokens, `.pem`, `.pfx`, `.key`, exported Azure tokens, or local credential caches.
- Prefer Entra ID (`DefaultAzureCredential`) over API keys.
- Only `NEXT_PUBLIC_*` variables are safe to expose to the browser. Do not put secrets in `NEXT_PUBLIC_*` variables.
- CORS is restricted by `CORS_ALLOW_ORIGINS`; avoid wildcard origins for shared environments.
- `backend/log/`, local uploads/downloads, temporary files, BI exports, and common credential file types are ignored.
- If a real secret was ever committed, rotate it in Azure before pushing to GitHub.

## Pre-Push Checklist

Run these before pushing:

```powershell
git status --short
git diff --stat
git check-ignore -v .env
python -c "import ast, pathlib; files=['backend/server.py','backend/dynamic_agent.py','backend/foundry_files.py','backend/foundry_runtime.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p) for p in files]; print('python syntax ok')"
docker-compose -f docker-compose.frontend.yml build
```

Manual checks:

- Confirm `.env` is not staged.
- Confirm any new data files are intentional.
- Confirm `frontend/public/favicon.ico` exists for the chat avatar and favicon route.
- Confirm backend can reach Azure AI Foundry with your logged-in identity.
- Confirm `/api/health` reports backend connectivity when backend is running.

## Notes for Reviewers

This repo is intended as an internal enterprise AI portal foundation. The current implementation prioritizes secure local configuration, clear Azure AI Foundry integration, file-aware agent runs, and a production-quality frontend layout. Secrets are expected to be supplied by local `.env`, CI/CD secret stores, or Azure managed identity, never by committed source files.
