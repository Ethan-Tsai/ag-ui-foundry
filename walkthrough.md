# AG-UI Foundry Walkthrough (Handoff for Next Codex Model)

Last updated: 2026-04-24

This document is a fast handoff for continuing work in this repository with minimal context loss.

## 1) What This Repo Is

`ag-ui-foundry` is a full-stack demo/product shell for AG-UI + Azure AI Foundry agents.

- Backend: FastAPI + `agent_framework_ag_ui`
- Frontend: Next.js + CopilotKit
- Primary runtime modes:
  - `AGENT_KIND=local`: local agent + tool call to Foundry agent
  - `AGENT_KIND=foundry`: dynamic router that switches Foundry agent by `agentId`

## 2) Current Functional Core (Important)

### AG-UI State Model
Defined in `backend/state.py` as PowerBI-oriented state:

- `dashboard_context: str`
- `current_insights: str`
- `data_points: list[DataPoint]`

Mapped tool state updates:

- `update_dashboard_context`
- `update_insights`
- `add_data_points`

## 3) Backend Architecture (Current)

### Entry
- `backend/server.py`

Key behavior:
- Adds middleware to capture `agentId` query param into `current_agent_id`.
- Mounts AG-UI endpoint at `/ag-ui` with `default_state=INITIAL_POWERBI_STATE`.
- Exposes helper APIs:
  - `GET /api/agents`
  - `GET /api/agent-info?agent_name=...`

### Dynamic Foundry Routing (Critical Fix Applied)
- `backend/dynamic_agent.py`

Current design:
- `DynamicRouterAgent` caches **raw `FoundryAgent` instances** per agent name.
- `run(messages=..., **kwargs)` forwards directly to `FoundryAgent.run(...)`.
- This avoids `AgentFrameworkAgent.run(... stream=...)` signature mismatch.

Why it matters:
- A prior crash was:
  - `TypeError: AgentFrameworkAgent.run() got an unexpected keyword argument 'stream'`
- Fix: only wrap once in `server.py`:
  - `AgentFrameworkAgent(agent=get_dynamic_router_agent(), state_schema=..., predict_state_config=...)`

### Foundry Metadata & Tooling
- `backend/foundry_agent.py`
  - `get_agent_metadata(...)` resolves name/description/welcome/prompts
- `backend/agent_tool.py`
  - builds cached `ask_agent(question, context)` tool for local mode

## 4) Frontend Architecture (Current)

### Main UI
- `frontend/app/page.tsx`

Current behavior:
- Loads agents from `/api/agents`
- Loads selected agent metadata from `/api/agent-info`
- Uses `CopilotKit` runtime `/api/copilotkit/default` and passes `agent={selectedAgent}`
- Uses `useCoAgent` to surface AG-UI shared state (snapshot panel in chat area)

Important mapping currently used:
- `welcomeMessage` -> display name/title
- `description` -> welcome/initial text

### CopilotKit Proxy
- `frontend/app/api/copilotkit/[integrationId]/route.ts`

Current behavior:
- Handles `info` method dynamically from backend agent list
- Injects/normalizes `agentId` into query params and request params
- Forwards payload to backend `AG_UI_ENDPOINT`

## 5) Recently Fixed Issues

1. Foundry streaming crash (`unexpected keyword argument 'stream'`)
- Resolved by routing to raw `FoundryAgent` in `dynamic_agent.py`, and wrapping router in `AgentFrameworkAgent` only in `server.py`.

2. Windows console encoding crash in startup logs
- Non-ASCII prints were removed/replaced with logger-safe output.

3. Frontend crash on `undefined.at(...)`
- In state snapshot, replaced `.at(-1)` with safe guarded indexing.

4. UI regressions (partial)
- Large visual redesign landed in `page.tsx` + `style.css`
- Height/layout constraints were improved, but this area is still under active tuning per user feedback.

## 6) Current User Priorities (Carry Forward)

The user explicitly wants:

1. Do **not** break AG-UI core state flow.
2. Keep conversation state clearly visible.
3. Improve frontend polish, but prioritize layout correctness:
   - Laptop default must show chat input without awkward page scrolling.
   - RWD should be stable.
4. Professional agent icons/visual differentiation.

## 7) Known Risk / Open Items

1. Frontend layout tuning is still iterative.
- Latest CSS greatly changed structure; verify on laptop sizes (especially around input visibility and panel heights).

2. Environment constraints on this machine:
- `npm/node` may be unavailable in shell, so local frontend build/lint may fail unless environment is fixed.

3. Repo is in a dirty working tree with many modified/untracked files.
- Avoid reverting unrelated changes.

## 8) Quick Start Commands

From repo root (`D:\CODE\ag-ui-foundry`):

```powershell
# Backend (preferred from venv)
.\.venv\Scripts\python.exe backend\server.py

# or if uv is configured
uv run python .\backend\server.py
```

Health checks:

- `GET http://localhost:8000/`
- `GET http://localhost:8000/api/agents`
- `GET http://localhost:8000/api/agent-info?agent_name=<name>`
- AG-UI endpoint: `POST http://localhost:8000/ag-ui`

## 9) File Map for Fast Continuation

Backend core:
- `backend/server.py`
- `backend/dynamic_agent.py`
- `backend/state.py`
- `backend/foundry_agent.py`
- `backend/local_agent.py`
- `backend/agent_tool.py`

Frontend core:
- `frontend/app/page.tsx`
- `frontend/app/style.css`
- `frontend/app/globals.css`
- `frontend/app/layout.tsx`
- `frontend/app/api/copilotkit/[integrationId]/route.ts`
- `frontend/app/api/agents/route.ts`
- `frontend/app/api/agent-info/route.ts`

## 10) Suggested Next Steps for the New Model

1. Validate AG-UI event/state flow first (no regressions).
2. Verify chat input visibility on common laptop resolutions.
3. Refine only layout/CSS where needed; avoid changing backend protocol paths unless broken.
4. Keep `agentId` routing behavior intact between frontend proxy and backend middleware.

