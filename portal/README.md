# Portal MVP (Phase 0/1)

Implements the revised design's foundation:

- Component catalog (`catalog/catalog.yaml`)
- Profile resolver + canonical profile hash
- Capability Exact / Superset matcher
- Build request APIs + HMAC internal callbacks
- Seeded Hot Preset READY images (factory closed)
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

## MVP scope notes

- `mvpFactoryEnabled: false` — unmatched profiles are rejected
- Jenkins/Windows Pod execution is stubbed (`BUILD_QUEUED`); advance via HMAC callback
- SSO/RBAC is not wired; send optional `X-Actor`
