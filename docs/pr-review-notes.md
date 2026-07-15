# PR Review Notes (2026-07-15)

## Verdict

**Request changes → addressed** (auth gates + lease CAS + factory lock + cutover paths).

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

## Cutover implemented

| Item | Status |
|------|--------|
| Real git commit resolve | `PORTAL_GIT_RESOLVE_MODE=placeholder\|ls_remote\|http_api` (+ `PORTAL_GIT_REQUIRE_EXACT`) |
| Windows checkout / NuGet / MSBuild | `jenkins/shared-library/scripts/windows/*.ps1` injected by project Jenkinsfile |
| Factory non-dry-run docker | `portal_factory_agent.py build` without `--dry-run` / `FACTORY_DRY_RUN=0` |
| Partial unique on active `profile_hash` | `build_image_profile_hash_active_uidx` (Postgres + SQLite where) |
| Actor auth | Bearer `PORTAL_API_TOKENS` + optional `PORTAL_REQUIRE_AUTH` (SSO still external IdP) |

## Still external / ops-owned

1. Wire Jenkins credentials `git-url-template`, `nuget-internal-feed-url`
2. Point Portal at real git (`ls_remote` or `http_api`) and set `PORTAL_GIT_REQUIRE_EXACT=true` in prod
3. Factory host: layouts/installers + Registry push ACL
4. Corporate SSO in front of Portal (tokens are service-account bridge until then)
