# Portal (Phase 0/1 + Factory orchestration)

Implements the revised design foundation and phase-2 factory control plane:

- Component catalog (`catalog/catalog.yaml`)
- Profile resolver + canonical profile hash
- Capability Exact / Superset matcher
- Build request APIs + HMAC internal callbacks
- Seeded Hot Preset READY images
- On-demand Image Factory lock/lease/waiters (Jenkins stub)
- Dockerfile / `.vsconfig` / install manifest generation
- Minimal React UI

## Backend

```bash
cd portal/backend
pip install -r requirements.txt
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

## Scope notes

- `mvpFactoryEnabled: true` — cold profiles queue `msbuild-image-factory` (Jenkins stub)
- Exact waiters share one CREATING lease; READY callback wakes them to project build
- Factory artifacts: `GET /internal/v1/images/{hash}/factory-artifacts`
- Lease heartbeat + reconcile endpoints for callback loss / TTL expiry
- Windows Docker build host is still external (scripts under `image_factory/scripts`)
- SSO/RBAC is not wired; send optional `X-Actor`
