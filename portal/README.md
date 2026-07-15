# Portal (Phase 0/1 + Factory orchestration + local simulation)

Implements the revised design control plane and a local end-to-end simulation path:

- Component catalog (`catalog/catalog.yaml`)
- Profile resolver + canonical profile hash
- Capability Exact / Superset matcher
- Build request APIs + HMAC internal callbacks
- Seeded Hot Preset READY images
- On-demand Image Factory lock/lease/waiters
- Dockerfile / `.vsconfig` / install manifest generation
- Local Factory/Project **worker simulation** (manual or `PORTAL_SIMULATE_WORKERS=true`)
- Optional real Jenkins HTTP trigger (`PORTAL_JENKINS_*`)
- Minimal React UI

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
```

## Frontend

```bash
cd portal/frontend
npm install
npm run dev
```

UI proxies `/api` to `http://127.0.0.1:8000`.

## Local complete path

1. Submit build request (Preset reuse → `BUILD_QUEUED`, cold → `IMAGE_BUILD_QUEUED`)
2. Click **Simulate workers** in UI, or:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/build-requests/{id}/simulate
   ```
3. Request advances to `SUCCEEDED` with simulated image digest

## Scope notes

- Simulation does **not** run Windows Docker/MSBuild; it completes Portal state transitions
- Real Jenkins: set `PORTAL_JENKINS_URL`, `PORTAL_JENKINS_USERNAME`, `PORTAL_JENKINS_API_TOKEN`
- Windows Factory host / K8s Pod build remain external
- SSO/RBAC is not wired; send optional `X-Actor`
