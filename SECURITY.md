# Security Policy

## Secret Handling

- Do not commit `.env`, `.env.*`, API keys, bearer tokens, certificate files, exported Azure tokens, or local credential caches.
- Use Azure Entra ID authentication through `DefaultAzureCredential` whenever possible.
- Store production secrets in CI/CD secret stores or Azure-managed secret services, not in source control.
- Only expose browser-safe values with the `NEXT_PUBLIC_` prefix.

## Local Configuration

Use `env.sample` as the template for local `.env` files. The local `.env` file is ignored by git.

If a secret is accidentally committed:

1. Rotate or revoke the secret in Azure or the issuing system.
2. Remove the secret from git history before publishing externally.
3. Re-run secret scanning before pushing.

## Recommended Pre-Push Checks

```powershell
git status --short
git diff --stat
git check-ignore -v .env
```

For Python syntax:

```powershell
python -c "import ast, pathlib; files=['backend/server.py','backend/dynamic_agent.py','backend/foundry_files.py','backend/foundry_runtime.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p) for p in files]; print('python syntax ok')"
```

For frontend build:

```powershell
docker-compose -f docker-compose.frontend.yml build
```

## Reporting

For internal review, document security concerns in the project issue tracker and avoid posting secrets or full tokens in screenshots, logs, or comments.
