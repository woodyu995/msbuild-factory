# Jenkins wiring guide (credentials, nodes, jobs)

## 1. Credentials

| ID | Type | Used by |
|----|------|---------|
| `portal-base-url` | Secret text | Factory/Project Jenkinsfiles |
| `portal-callback-hmac` | Secret text | Agent HMAC callbacks |
| `portal-api-token` | Secret text | Project `resolve`/`pod-template` Bearer (`jenkins-bot`) |
| `portal-jenkins-api` | Username/password | Portal `HttpJenkinsClient` |
| `git-url-template` | Secret text | Project checkout (`https://git/{repository}.git`) |
| `nuget-internal-feed-url` | Secret text | Windows NuGet restore |

Portal env must match:

```bash
PORTAL_CALLBACK_HMAC_SECRET=...
PORTAL_JENKINS_URL=https://jenkins.internal/
PORTAL_JENKINS_USERNAME=portal-bot
PORTAL_JENKINS_API_TOKEN=...
PORTAL_GIT_RESOLVE_MODE=ls_remote
PORTAL_GIT_URL_TEMPLATE=https://git.internal/{repository}.git
PORTAL_GIT_REQUIRE_EXACT=true
PORTAL_REQUIRE_AUTH=true
PORTAL_API_TOKENS=name:token:role1|role2,...
```

## 2. Nodes / clouds

- Permanent Windows node label: `msbuild-factory`
- Kubernetes cloud namespace: `msbuild-build`
- Windows worker labels: see `k8s/windows/README.md`

## 3. Jobs

Seed from `jenkins/README.md` Job DSL, or create Pipeline jobs pointing at:

- `jenkins/jobs/msbuild-image-factory/Jenkinsfile`
- `jenkins/jobs/msbuild-project-build/Jenkinsfile`

## 4. Smoke test (no Windows docker)

1. Start Portal with Jenkins URL unset (recording client)
2. Create cold build request → `IMAGE_BUILD_QUEUED`
3. Run factory agent dry-run → READY / `BUILD_QUEUED`
4. `GET /api/v1/build-requests/{id}/pod-template` → YAML with digest
5. Optional local only: `PORTAL_SIMULATE_WORKERS=true` then
   `POST /api/v1/build-requests/{id}/simulate` → `SUCCEEDED`

## 5. Production cutover

1. Apply `k8s/portal/namespace.yaml`, Postgres STS, Portal Deployment (see `k8s/portal/README.md`)
2. Point `PORTAL_DATABASE_URL` at Postgres Service DNS
3. Set `PORTAL_REGISTRY_*` to Nexus docker connector; create hosted repos + `nexus-docker` Jenkins cred
4. Apply `jenkins/casc/jenkins.yaml` (edit secrets; `portal-base-url` = Portal Service DNS)
5. Apply `k8s/windows/rbac.yaml` + `nexus-docker-pull` secret
6. Label Windows node pools; keep factory on permanent Windows host
7. Set Portal `PORTAL_JENKINS_*` to Jenkins Service DNS
8. Set Portal git resolve + `PORTAL_GIT_REQUIRE_EXACT=true`
9. Set `PORTAL_REQUIRE_AUTH=true` + `PORTAL_API_TOKENS`
10. Set `PORTAL_ALLOW_INSECURE_DEFAULTS=false` + strong HMAC
11. Disable `PORTAL_SIMULATE_WORKERS`
12. Set `FACTORY_DRY_RUN=0` on factory host
13. Create Jenkins credentials `git-url-template`, `nuget-internal-feed-url`, `portal-api-token`, `nexus-docker`
14. Seed `portal-lease-reconcile` job **or** rely on in-cluster CronJob in `k8s/portal/deployment.yaml`

### Local smoke (no Windows)

```bash
cd portal/backend && pip install -r requirements.txt
bash portal/scripts/smoke-cutover.sh
```
