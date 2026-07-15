# PR Review Notes (2026-07-15)

## Verdict

**Request changes → addressed** (auth gates + lease CAS + factory lock + cutover + follow-up races).

## Findings fixed

| Severity | Issue | Fix |
|----------|-------|-----|
| Critical | Unauthenticated simulate / artifacts / reconcile | Simulate gated by `PORTAL_SIMULATE_WORKERS`; artifacts/reconcile require HMAC |
| High | Lease CAS skipped when `lease_id` null | Mutating callbacks require exact active lease match |
| High | Factory duplicate CREATING risk | `FOR UPDATE` + deterministic active-row selection; avoid duplicate inserts when row exists |
| High | `FACTORY_BUSY` looked like queued | Status `FACTORY_BUSY` (terminal) |
| High | Jenkinsfile ignored Portal pod-template | Scripted `podTemplate(yaml: portalYaml)` |
| Medium | Weak HMAC compare / default secret | Length-safe compare; refuse default secret unless `ALLOW_INSECURE_DEFAULTS` |
| Medium | `solutionPath` traversal | Allowlist validation |
| Medium | Arbitrary build-event jumps | Transition allow-list |

## Cutover + follow-up review fixes

| Severity | Issue | Fix |
|----------|-------|-----|
| Critical | Factory `fetch` missing HMAC | Jenkinsfile passes `--hmac-secret`; agent env fallback |
| High | HMAC ambient in Windows project pod | `withCredentials` scoped per step; MSBuild has no HMAC |
| High | Internal simulate ungated | `/internal/v1/simulate/*` requires HMAC |
| High | Default actor was admin/operator | Default roles = `builder` only |
| High | MSBuild `/p:` injection | Portal + PS1 allowlists for configuration/platform |
| High | Project resolve/pod-template no Bearer | Agent `--api-token` + Jenkins `portal-api-token` |
| High | Same-hash cold insert race | Re-read after slot lock + IntegrityError → WAIT |
| Medium | factory-artifacts unbound to lease | `leaseId` required + active CREATING/VALIDATING check |
| Medium | Lease CAS ignored expiry | Status/heartbeat/artifacts reject expired leases |
| Medium | Bearer length mismatch 500 | Length-safe token compare → 401 |
| Medium | Idempotency ignores payload | Fingerprint + 409 on conflict |
| Medium | Idempotency concurrent insert 500 | Savepoint + reselect on IntegrityError |
| Medium | Global factory slot race | `factory_control` singleton `FOR UPDATE` |
| Medium | Status/heartbeat missing row lock | `FOR UPDATE` on image rows |
| Medium | `require_auth` create-only | Applied to all `/api/v1/*` routes |
| Medium | No reconcile cron | `portal-lease-reconcile` Jenkins job |
| Medium | Fake TESTING/PUBLISHING success | Pipeline posts SUCCEEDED after MSBuild only |

## Still external / ops-owned

1. Wire Jenkins credentials `git-url-template`, `nuget-internal-feed-url`, `portal-api-token`
2. Point Portal at real git (`ls_remote` or `http_api`) and set `PORTAL_GIT_REQUIRE_EXACT=true` in prod
3. Factory host: layouts/installers + Registry push ACL
4. Corporate SSO in front of Portal (tokens are service-account bridge until then)
5. Set `PORTAL_ALLOW_INSECURE_DEFAULTS=false` and strong HMAC in prod
6. Local Simulate UI: operator Bearer token (UI token field) or `PORTAL_DEFAULT_ACTOR_ROLES=operator`
7. Real Windows Docker / K8s smoke on factory + worker pools
8. Wire real test/publish stages when product requires them
