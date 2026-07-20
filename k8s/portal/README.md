# Portal + Postgres on Kubernetes (Nexus registry)

## Topology

```text
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ portal (Deploy) │────▶│ postgres (STS)   │     │ nexus (existing)│
│ msbuild-portal  │     │ msbuild-portal   │     │ docker hosted   │
└────────┬────────┘     └──────────────────┘     └────────▲────────┘
         │  ClusterDNS                                    │
         ▼                                                │ docker push
┌─────────────────┐     ┌──────────────────┐              │
│ jenkins (pod)   │────▶│ Windows factory  │──────────────┘
│ + K8s agents    │     │ (dedicated host) │
└─────────────────┘     └──────────────────┘
```

## Apply order

```bash
kubectl apply -f k8s/portal/namespace.yaml
# Create secrets from the example (edit passwords first):
#   portal-secrets, portal-callback-hmac, postgres-secrets,
#   nexus-docker-pull (msbuild-portal AND msbuild-build)
kubectl apply -f k8s/portal/secret.example.yaml   # after editing — or use SealedSecrets
kubectl apply -f k8s/postgres/statefulset.yaml
kubectl apply -f k8s/portal/configmap.yaml
kubectl apply -f k8s/portal/deployment.yaml
```

Build/push Portal image to Nexus:

```bash
docker build -f portal/Dockerfile -t nexus.company.io/build/msbuild-portal:latest .
docker push nexus.company.io/build/msbuild-portal:latest
```

## Env contract

| Variable | Example | Notes |
|----------|---------|-------|
| `PORTAL_DATABASE_URL` | `postgresql+psycopg://portal:***@postgres.msbuild-portal.svc:5432/portal` | |
| `PORTAL_JENKINS_URL` | `http://jenkins.jenkins.svc.cluster.local:8080/` | in-cluster OK |
| `PORTAL_REGISTRY_HOST` | `nexus.company.io:8082` | **Must resolve on factory host + Windows workers** (avoid ClusterDNS-only names) |
| `PORTAL_REGISTRY_PUSH_HOST` | (optional) | Override factory `docker login`/push when DNS differs |
| `PORTAL_REGISTRY_FINAL_REPO` | `build/msbuild-profile` | |
| `PORTAL_REGISTRY_STAGING_REPO` | `build/msbuild-profile-staging` | |
| `PORTAL_REGISTRY_PULL_SECRET` | `nexus-docker-pull` | name used in pod templates |

Jenkins `nexus-docker` credential must match the push host. Create `nexus-docker-pull` in **both** `msbuild-portal` and `msbuild-build`.

## Nexus Docker repo

1. Create **docker (hosted)** repos: `build-msbuild-profile`, `build-msbuild-profile-staging` (or a single hosted + path)
2. Expose docker connector (e.g. port 8082)
3. Factory host: `docker login <PORTAL_REGISTRY_PUSH_HOST or PORTAL_REGISTRY_HOST>` via Jenkins `nexus-docker`
4. Windows workers: `imagePullSecrets: nexus-docker-pull`

## Probes

- Liveness: `GET /livez` (no DB)
- Readiness: `GET /readyz` (fails if Postgres unreachable)
- Status: `GET /healthz` (includes registry hosts)
