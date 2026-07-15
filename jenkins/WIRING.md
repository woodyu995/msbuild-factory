# Jenkins wiring guide (credentials, nodes, jobs)

## 1. Credentials

| ID | Type | Used by |
|----|------|---------|
| `portal-base-url` | Secret text | Factory/Project Jenkinsfiles |
| `portal-callback-hmac` | Secret text | Agent HMAC callbacks |
| `portal-jenkins-api` | Username/password | Portal `HttpJenkinsClient` |

Portal env must match:

```bash
PORTAL_CALLBACK_HMAC_SECRET=...
PORTAL_JENKINS_URL=https://jenkins.internal/
PORTAL_JENKINS_USERNAME=portal-bot
PORTAL_JENKINS_API_TOKEN=...
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
5. Disable `PORTAL_SIMULATE_WORKERS`
6. Set `FACTORY_DRY_RUN=0` on factory host
