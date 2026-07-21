# PR #7 review notes (air-gap factory path)

## Confirmed root cause of recurring `UnicodeDecodeError`

Tracebacks pointed at:

```text
portal_factory_agent.py → subprocess.run → communicate → stdout.read()
UnicodeDecodeError
```

On Korean Windows, Python 3.14 defaults text-mode pipes to **cp949**. Docker build emits ANSI / mixed bytes that are not valid cp949.  

Evidence the host was still on a stale agent:

| Signal | Stale agent | Current agent (`AGENT_REV=20260721-binlog-v3`) |
|--------|-------------|-----------------------------------------------|
| `built = subprocess.run` line | ~422 with `text=True` | binary `Popen` via `_run_docker_build_to_log` |
| Startup JSON | missing | `{"phase":"agent-start","agentRev":"20260721-binlog-v3",...}` |
| Path in traceback | `...\msbuild-service\msbuild-factory\...` | must be **that exact file** overwritten |

Fix in this PR: **no text-mode pipes** for docker build; stream bytes to `docker-build.log`.

## Other air-gap issues already addressed in branch

1. **Lease expired / slots full** — Ensure soft-rearm, 12h airgap lease, heartbeat during build, reconcile expired CREATING.
2. **`--build-context` unknown** — detect + robocopy embed fallback.
3. **PowerShell `$layoutRoot\vs_...` parse error** — fixed message formatting; cmd `#` → `REM`.
4. **VS exit 5003 InvalidCertificate** — import layout certs + **Microsoft Windows Code Signing PCA 2024** under `scripts/certs/` + disable CRL.

## Remaining risks / follow-ups

1. **Portal image freshness** — UI Ensure still emitting `VC.MFC` means Linux Portal image may be older than catalog (script remaps to `VC.ATLMFC`, but Portal should be rebuilt).
2. **Operator copy discipline** — multiple trees (`msbuild-service\msbuild-factory` vs `C:\msbuild-factory`); always overwrite the path printed in `argv0`.
3. **Docker `COPY scripts` cache** — after updating scripts/certs, delete `$WORK\scripts` and set `FACTORY_DOCKER_BUILD_NO_CACHE=1`.
4. **If 5003 persists after PCA 2024** — verify `vs_installer.opc` integrity vs internet layout; consider pre-baking VS into `msbuild-agent-base` on an internet Windows Docker host (`FACTORY_SKIP_VS_INSTALL=1` for profile builds).

## Verify on Windows before long build

```powershell
python $agent --help
# stdout must contain: "agentRev": "20260721-binlog-v3"

Select-String -Path $agent -Pattern "binlog-v3|_run_docker_build_to_log"
```
