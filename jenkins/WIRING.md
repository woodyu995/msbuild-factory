# Jenkins wiring guide (credentials, nodes, jobs)

## 1. Credentials

| ID | Type | Used by |
|----|------|---------|
| `portal-base-url` | Secret text | Factory/Project Jenkinsfiles |
| `portal-callback-hmac` | Secret text | Agent HMAC callbacks |
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

1. Apply `jenkins/casc/jenkins.yaml` (edit secrets)
2. Apply `k8s/windows/rbac.yaml` + pull secret
3. Label Windows node pools
4. Set Portal `PORTAL_JENKINS_*`
5. Set Portal git resolve to `ls_remote` or `http_api` and `PORTAL_GIT_REQUIRE_EXACT=true`
6. Set `PORTAL_REQUIRE_AUTH=true` + `PORTAL_API_TOKENS`
7. Disable `PORTAL_SIMULATE_WORKERS`
8. Set `FACTORY_DRY_RUN=0` on factory host
9. Create Jenkins credentials `git-url-template` and `nuget-internal-feed-url`
