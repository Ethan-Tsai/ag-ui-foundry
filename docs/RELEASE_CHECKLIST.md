# Release Checklist

Use this checklist before pushing the repository to GitHub or sharing it for management review.

## Source Control

- [ ] `git status --short` reviewed.
- [ ] `.env` and `.env.*` are not staged.
- [ ] Generated files, local logs, exported BI files, and temporary files are not staged.
- [ ] New untracked files are intentional and should be part of the repository.

## Secrets

- [ ] Real API keys, bearer tokens, passwords, client secrets, certificates, and local Azure token caches are not committed.
- [ ] `env.sample` contains placeholders only.
- [ ] Any secret previously committed has been rotated before publishing.
- [ ] No secrets are stored in `NEXT_PUBLIC_*` variables.

## Runtime Configuration

- [ ] `AZURE_AI_PROJECT_ENDPOINT` points to the intended Azure AI Foundry project.
- [ ] `AZURE_AI_PROJECT_AGENT_NAME` points to the intended default/router agent.
- [ ] `AG_UI_ENDPOINT` is correct for the run mode.
- [ ] `CORS_ALLOW_ORIGINS` is restricted to expected frontend origins.
- [ ] `LOG_LEVEL` is set to `INFO` for normal use.
- [ ] `ENABLE_DEBUG_LOGGING` is disabled unless local file logging is required.

## Validation

Run syntax validation:

```powershell
python -c "import ast, pathlib; files=['backend/server.py','backend/dynamic_agent.py','backend/foundry_files.py','backend/foundry_runtime.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p) for p in files]; print('python syntax ok')"
```

Run frontend build when Docker/network is available:

```powershell
docker-compose -f docker-compose.frontend.yml build
```

Optional frontend-only local run:

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

Optional backend local run:

```powershell
python backend/server.py --agent foundry
```

## Manual UI Checks

- [ ] Agent Catalog loads and scrolls independently.
- [ ] Current Agent header shows the selected agent.
- [ ] Chat input is fully visible at the bottom of the conversation panel.
- [ ] Assistant and user messages show distinct avatars and sender names.
- [ ] File upload shows uploading/success/error state.
- [ ] Generated file download links render as file cards.
- [ ] Mobile/tablet layout avoids horizontal overflow.

## Reviewer Notes

- `README.md` explains architecture, setup, security, and run modes.
- `SECURITY.md` explains secret handling and reporting expectations.
- `env.sample` is the only committed environment template.
- `.gitignore`, root `.dockerignore`, `frontend/.dockerignore`, and `backend/.dockerignore` exclude local secrets and generated artifacts.
