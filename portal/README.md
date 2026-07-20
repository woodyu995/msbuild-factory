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

## Local / air-gap step verify (no login / Jenkins / Nexus)

- **폐쇄망 (이미지 빌드까지):** **[docs/airgap-local-verify-tutorial.md](../docs/airgap-local-verify-tutorial.md)**
- 인터넷망 바로 확인: **[docs/local-verify-tutorial.md](../docs/local-verify-tutorial.md)**

```bash
# internet: build + USB
docker compose -f docker-compose.local.yml build
docker tag msbuild-factory-portal:latest msbuild-portal:airgap
docker save msbuild-portal:airgap -o msbuild-portal.tar

# closed network:
docker load -i msbuild-portal.tar
docker compose -f docker-compose.airgap-local.yml up -d
# open http://<host>:8000/ → Ensure → ./local-images/*.tar
```

## Air-gap + Jenkins + Nexus (later stage)

Follow **[docs/airgap-docker-image-factory-tutorial.md](../docs/airgap-docker-image-factory-tutorial.md)** after the local-factory verify.

```bash
# on closed-network Linux Docker host (after USB docker load)
cp .env.airgap.example .env.airgap   # edit
docker compose -f docker-compose.airgap.yml --env-file .env.airgap up -d
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
| `PORTAL_SIMULATE_WORKERS` | Fake READY digests (no Docker) |
| `PORTAL_LOCAL_FACTORY` | Real local `docker build` + `docker save` (no Nexus) |
| `PORTAL_LOCAL_IMAGES_DIR` | Tar output dir (default `/var/portal-local-images`) |
| `PORTAL_JENKINS_*` | Real Jenkins trigger (later) |
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
