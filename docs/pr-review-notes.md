# PR Review Notes (2026-07-15)

## Verdict

**Request changes → addressed in follow-up commit.**

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

## Remaining (next)

1. Real git commit resolve (replace `resolved:{ref}` placeholder)
2. Real checkout / NuGet / MSBuild in Jenkins Windows container
3. Factory host non-dry-run docker build path
4. Partial unique DB index on active `profile_hash` (Postgres)
5. Strong identity for `X-Actor` / SSO
