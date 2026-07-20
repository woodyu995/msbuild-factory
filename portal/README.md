# Portal (Phase 0/1 + Factory orchestration + local simulation)

Implements the revised design control plane and a local end-to-end simulation path:

- Component catalog (`catalog/catalog.yaml`)
- Profile resolver + canonical profile hash
- Capability Exact / Superset matcher
- Build request APIs + HMAC internal callbacks
- Seeded Hot Preset READY images
- On-demand Image Factory lock/lease/waiters
- Dockerfile / `.vsconfig` / install manifest generation
- Pluggable git commit resolve (`placeholder` / `ls_remote` / `http_api`)
- Optional Bearer API tokens (`PORTAL_API_TOKENS`)
- Local Factory/Project **worker simulation** (manual or `PORTAL_SIMULATE_WORKERS=true`)
- Optional real Jenkins HTTP trigger (`PORTAL_JENKINS_*`)
- Minimal React UI

## Internet-side quick verify (Intel macOS + Docker)

Follow **[docs/local-internet-verify-tutorial.md](../docs/local-internet-verify-tutorial.md)** (Intel Mac 기준).

```bash
# from repo root — Docker Desktop running
docker compose -f docker-compose.local.yml up --build
# open http://127.0.0.1:8000/
```

## Backend

```bash
cd portal/backend
pip install -r requirements.txt
# optional local demo auto-complete:
# export PORTAL_SIMULATE_WORKERS=true
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

Tests:

```bash
cd portal/backend
PYTHONPATH=. pytest -q
# cutover smoke (in-process TestClient, no live server needed):
bash ../scripts/smoke-cutover.sh
```

### Key env vars

| Variable | Purpose |
|----------|---------|
| `PORTAL_GIT_RESOLVE_MODE` | `placeholder` (default), `ls_remote`, `http_api` |
| `PORTAL_GIT_URL_TEMPLATE` | e.g. `https://git/{repository}.git` for `ls_remote` |
| `PORTAL_GIT_REQUIRE_EXACT` | Reject placeholder commits (prod) |
| `PORTAL_REQUIRE_AUTH` | Require `Authorization: Bearer …` |
| `PORTAL_API_TOKENS` | `name:token:role1\|role2,…` |
| `PORTAL_DEFAULT_ACTOR_ROLES` | Default when auth off (default `builder`; use `operator` for local Simulate UI) |
| `PORTAL_SIMULATE_WORKERS` | Local auto-advance only |
| `PORTAL_JENKINS_*` | Real Jenkins trigger |
| `PORTAL_DATABASE_URL` | Postgres URL (`postgresql+psycopg://…`) in cluster |
| `PORTAL_REGISTRY_HOST` | Nexus docker connector host:port |
| `PORTAL_REGISTRY_FINAL_REPO` | e.g. `build/msbuild-profile` |
| `PORTAL_REGISTRY_STAGING_REPO` | e.g. `build/msbuild-profile-staging` |

## Frontend

```bash
cd portal/frontend
npm install
npm run dev
```

UI proxies `/api` to `http://127.0.0.1:8000`.

## Local complete path (API split B)

1. `POST /api/v1/images/ensure` with environment → reuse READY or start factory
2. Poll `GET /api/v1/images/{profileHash}` until `ready: true` (or Simulate factory)
3. `POST /api/v1/build-requests` with project + environment + required `matchedProfileHash` / `imageDigest` → `BUILD_QUEUED`
4. Click **Simulate project** in UI, or:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/build-requests/{id}/simulate
   ```
5. Request advances to `SUCCEEDED` with simulated image digest

Cold profiles return `409 IMAGE_NOT_READY` from build-requests until ensure finishes.

## Scope notes

- Simulation does **not** run Windows Docker/MSBuild; it completes Portal state transitions
- Real Jenkins: set `PORTAL_JENKINS_URL`, `PORTAL_JENKINS_USERNAME`, `PORTAL_JENKINS_API_TOKEN`
- Real commits: set `PORTAL_GIT_RESOLVE_MODE` + `PORTAL_GIT_REQUIRE_EXACT=true`
- Factory host: see `docs/factory-host-runbook.md` (`FACTORY_DRY_RUN=0`)
- Project Windows scripts: `jenkins/shared-library/scripts/windows/`
- Auth: Bearer tokens until corporate SSO is fronted; optional `X-Actor` only when auth is off
