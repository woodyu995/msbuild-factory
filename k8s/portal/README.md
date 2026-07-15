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
kubectl apply -f k8s/postgres/statefulset.yaml   # edit POSTGRES_PASSWORD first
# edit secret.example.yaml → portal-secrets (+ nexus pull secret in msbuild-build)
kubectl apply -f k8s/portal/secret.example.yaml  # after editing
kubectl apply -f k8s/portal/configmap.yaml
kubectl apply -f k8s/portal/deployment.yaml
```

Build/push Portal image to Nexus:

```bash
docker build -f portal/Dockerfile -t nexus.company.io/build/msbuild-portal:latest .
docker push nexus.company.io/build/msbuild-portal:latest
```

## Env contract

| Variable | Example |
|----------|---------|
| `PORTAL_DATABASE_URL` | `postgresql+psycopg://portal:***@postgres.msbuild-portal.svc:5432/portal` |
| `PORTAL_JENKINS_URL` | `http://jenkins.jenkins.svc.cluster.local:8080/` |
| `PORTAL_REGISTRY_HOST` | `nexus.nexus.svc.cluster.local:8082` |
| `PORTAL_REGISTRY_FINAL_REPO` | `build/msbuild-profile` |
| `PORTAL_REGISTRY_STAGING_REPO` | `build/msbuild-profile-staging` |

Jenkins credentials must use the same ClusterDNS for `portal-base-url`, and `nexus-docker` username/password for factory push.

## Nexus Docker repo

1. Create **docker (hosted)** repos: `build-msbuild-profile`, `build-msbuild-profile-staging` (or a single hosted + path)
2. Expose docker connector (e.g. port 8082) — that host:port is `PORTAL_REGISTRY_HOST`
3. Factory host: `docker login <PORTAL_REGISTRY_HOST>` via Jenkins `nexus-docker` credential
4. Windows workers: `imagePullSecrets: nexus-docker-pull` (see `k8s/windows`)

## Probes

- Liveness: `GET /healthz`
- Readiness: `GET /readyz` (fails if Postgres unreachable)
