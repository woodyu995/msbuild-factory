import { useEffect, useMemo, useState } from "react";

type Environment = {
  visualStudio: string;
  dotnetFrameworks: string[];
  dotnetSdks: string[];
  cppToolsets: string[];
  windowsSdks: string[];
  features: string[];
  reuseMode: "preferCompatible" | "exactReuse";
};

type Options = {
  catalogVersion: string;
  presets: Array<{
    id: string;
    displayName: string;
    hot?: boolean;
    environment: Omit<Environment, "reuseMode">;
  }>;
  visualStudios: Array<{
    id: string;
    windowsBase: string;
    allowed: {
      dotnetFrameworks: string[];
      dotnetSdks: string[];
      cppToolsets: string[];
      windowsSdks: string[];
      features: string[];
    };
  }>;
  mvpFactoryEnabled: boolean;
  simulateWorkers?: boolean;
  localFactory?: boolean;
  requireAuth?: boolean;
};

type EnsureResult = {
  requestedProfileHash: string;
  matchedProfileHash?: string;
  matchType?: string;
  action: string;
  imageStatus: string;
  ready: boolean;
  estimatedWaitMinutes?: number;
  providedCapabilities?: string[];
  extraCapabilities?: string[];
  errorMessage?: string;
  image?: { repository: string; tag: string; digest: string };
  factoryLeaseId?: string;
  localImageRef?: string;
  localTarPath?: string;
  localTarFile?: string;
};

type BuildRequest = {
  id: string;
  status: string;
  matchType: string;
  requestedProfileHash: string;
  matchedProfileHash?: string;
  imageDigest?: string;
  errorMessage?: string;
};

const emptyEnv: Environment = {
  visualStudio: "2022",
  dotnetFrameworks: ["4.8"],
  dotnetSdks: [],
  cppToolsets: [],
  windowsSdks: [],
  features: ["managed-desktop"],
  reuseMode: "preferCompatible",
};

const TOKEN_KEY = "portalApiToken";

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function loadToken(): string {
  return (
    localStorage.getItem(TOKEN_KEY) ||
    import.meta.env.VITE_PORTAL_API_TOKEN ||
    ""
  );
}

/** Only send Bearer when auth is required — stale tokens break local/open mode. */
function apiHeaders(
  extra: Record<string, string> = {},
  token: string,
  requireAuth: boolean,
): HeadersInit {
  const headers: Record<string, string> = { ...extra };
  if (requireAuth && token) {
    headers.Authorization = `Bearer ${token}`;
  } else {
    headers["X-Actor"] = "portal-ui";
  }
  return headers;
}

function detailMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    return JSON.stringify(detail);
  }
  return JSON.stringify(data);
}

export default function App() {
  const [options, setOptions] = useState<Options | null>(null);
  const [env, setEnv] = useState<Environment>(emptyEnv);
  const [project, setProject] = useState({
    repository: "ProductClient",
    gitRef: "release/2.1",
    solutionPath: "ProductClient.sln",
    configuration: "Release",
    platform: "x64",
  });
  const [apiToken, setApiToken] = useState(loadToken);
  const [ensureResult, setEnsureResult] = useState<EnsureResult | null>(null);
  const [buildResult, setBuildResult] = useState<BuildRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const localMode = Boolean(options?.localFactory);
  const requireAuth = Boolean(options?.requireAuth);

  function updateEnv(next: Environment) {
    setEnv(next);
    setEnsureResult(null);
    setBuildResult(null);
    setError(null);
  }

  useEffect(() => {
    if (requireAuth) {
      localStorage.setItem(TOKEN_KEY, apiToken);
    }
  }, [apiToken, requireAuth]);

  // Open mode: never send Bearer (stale tokens 401). Auth mode: send token.
  useEffect(() => {
    let cancelled = false;
    async function loadOptions() {
      // Prefer open probe first so local verify ignores stale localStorage tokens.
      let resp = await fetch("/api/v1/build-environment/options", {
        headers: apiHeaders({}, "", false),
      });
      if (resp.status === 401) {
        resp = await fetch("/api/v1/build-environment/options", {
          headers: apiHeaders({}, apiToken || loadToken(), true),
        });
      }
      if (!resp.ok) throw new Error(await resp.text());
      const data = (await resp.json()) as Options;
      if (!cancelled) setOptions(data);
    }
    loadOptions().catch((err) => {
      if (!cancelled) setError(String(err));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- bootstrap once; token reloads below
  }, []);

  useEffect(() => {
    if (!requireAuth) return;
    fetch("/api/v1/build-environment/options", {
      headers: apiHeaders({}, apiToken, true),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text());
        return r.json();
      })
      .then(setOptions)
      .catch((err) => setError(String(err)));
  }, [apiToken, requireAuth]);

  useEffect(() => {
    const hash = ensureResult?.matchedProfileHash;
    if (!hash || ensureResult?.ready) return;
    if (!["CREATING", "VALIDATING"].includes(ensureResult?.imageStatus || "")) return;

    const timer = window.setInterval(() => {
      fetch(`/api/v1/images/${hash}`, {
        headers: apiHeaders({}, apiToken, requireAuth),
      })
        .then(async (r) => {
          if (!r.ok) return;
          return r.json();
        })
        .then((data) => {
          if (!data) return;
          setEnsureResult((prev) =>
            prev
              ? {
                  ...prev,
                  imageStatus: data.imageStatus,
                  ready: data.ready,
                  image: data.image,
                  factoryLeaseId: data.factoryLeaseId,
                  matchedProfileHash: data.profileHash,
                  localImageRef: data.localImageRef,
                  localTarPath: data.localTarPath,
                  localTarFile: data.localTarFile,
                  action: data.ready ? "REUSE_EXACT" : prev.action,
                }
              : prev,
          );
        })
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [
    ensureResult?.matchedProfileHash,
    ensureResult?.ready,
    ensureResult?.imageStatus,
    apiToken,
    requireAuth,
  ]);

  useEffect(() => {
    if (localMode || !buildResult?.id) return;
    const terminal = new Set([
      "SUCCEEDED",
      "PROFILE_REJECTED",
      "PROJECT_BUILD_FAILED",
      "TEST_FAILED",
      "CANCELLED",
      "IMAGE_BUILD_FAILED",
    ]);
    if (terminal.has(buildResult.status)) return;
    const timer = window.setInterval(() => {
      fetch(`/api/v1/build-requests/${buildResult.id}`, {
        headers: apiHeaders({}, apiToken, requireAuth),
      })
        .then((r) => r.json())
        .then((data) => setBuildResult(data))
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [buildResult?.id, buildResult?.status, apiToken, requireAuth, localMode]);

  const vs = useMemo(
    () => options?.visualStudios.find((item) => item.id === env.visualStudio),
    [options, env.visualStudio],
  );

  async function onEnsure() {
    setBusy(true);
    setError(null);
    setBuildResult(null);
    try {
      const resp = await fetch("/api/v1/images/ensure", {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }, apiToken, requireAuth),
        body: JSON.stringify({ environment: env }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        const msg = detailMessage(data);
        if (resp.status === 503) {
          setError(`${msg} — Ensure를 다시 눌러 재시도하세요.`);
        } else {
          setError(msg);
        }
        setEnsureResult(null);
        return;
      }
      setEnsureResult(data);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSimulateFactory() {
    const hash = ensureResult?.matchedProfileHash || ensureResult?.requestedProfileHash;
    if (!hash) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(`/api/v1/images/${hash}/simulate`, {
        method: "POST",
        headers: apiHeaders({}, apiToken, requireAuth),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(detailMessage(data));
        return;
      }
      const status = await fetch(`/api/v1/images/${hash}`, {
        headers: apiHeaders({}, apiToken, requireAuth),
      });
      const image = await status.json();
      setEnsureResult((prev) =>
        prev
          ? {
              ...prev,
              imageStatus: image.imageStatus,
              ready: image.ready,
              image: image.image,
              matchedProfileHash: image.profileHash,
              localImageRef: image.localImageRef,
              localTarPath: image.localTarPath,
              localTarFile: image.localTarFile,
              action: image.ready ? "REUSE_EXACT" : prev.action,
            }
          : prev,
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onStartBuild() {
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/v1/build-requests", {
        method: "POST",
        headers: apiHeaders(
          {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          apiToken,
          requireAuth,
        ),
        body: JSON.stringify({
          project,
          environment: env,
          nuget: { mode: "repo-packages-and-internal-feed" },
          matchedProfileHash: ensureResult?.matchedProfileHash,
          imageDigest: ensureResult?.image?.digest,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(detailMessage(data));
        return;
      }
      setBuildResult(data);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSimulateBuild() {
    if (!buildResult?.id) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(`/api/v1/build-requests/${buildResult.id}/simulate`, {
        method: "POST",
        headers: apiHeaders({}, apiToken, requireAuth),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(detailMessage(data));
        return;
      }
      const refreshed = await fetch(`/api/v1/build-requests/${buildResult.id}`, {
        headers: apiHeaders({}, apiToken, requireAuth),
      });
      setBuildResult(await refreshed.json());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const imageReady = Boolean(ensureResult?.ready && ensureResult?.image);

  return (
    <div className="app">
      <header className="brand">
        <h1>MSBuild Build Portal</h1>
        <p>
          {localMode
            ? "환경을 고르고 Ensure image를 누르면 로컬에 이미지가 빌드·저장됩니다. (로그인/Nexus/Jenkins 없음)"
            : "1) Ensure image → 2) READY 후 Start build."}
          {options?.simulateWorkers ? " · simulate ON" : ""}
          {requireAuth ? " · auth required" : ""}
        </p>
      </header>

      <div className="grid">
        <section className="panel">
          <h2>빌드 환경</h2>
          {requireAuth && (
            <div className="field">
              <label>API token (Bearer)</label>
              <input
                type="password"
                value={apiToken}
                placeholder="required"
                onChange={(e) => setApiToken(e.target.value)}
                autoComplete="off"
              />
            </div>
          )}
          {options && (
            <div className="presets">
              {options.presets.map((preset) => (
                <button
                  key={preset.id}
                  className="preset"
                  type="button"
                  onClick={() =>
                    updateEnv({
                      ...preset.environment,
                      reuseMode: env.reuseMode,
                    })
                  }
                >
                  <strong>{preset.displayName}</strong>
                  <span>
                    {preset.id}
                    {preset.hot ? " · Hot" : ""}
                  </span>
                </button>
              ))}
            </div>
          )}

          <div className="field">
            <label>Visual Studio</label>
            <select
              value={env.visualStudio}
              onChange={(e) =>
                updateEnv({
                  ...emptyEnv,
                  visualStudio: e.target.value,
                  reuseMode: env.reuseMode,
                })
              }
            >
              {(options?.visualStudios || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} ({item.windowsBase})
                </option>
              ))}
            </select>
          </div>

          {(
            [
              ["dotnetFrameworks", "Targeting Pack"],
              ["dotnetSdks", ".NET SDK"],
              ["cppToolsets", "C++ Toolset"],
              ["windowsSdks", "Windows SDK"],
              ["features", "Features"],
            ] as const
          ).map(([key, label]) => (
            <div className="field" key={key}>
              <label>{label}</label>
              <div className="chips">
                {(vs?.allowed[key] || []).map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={`chip ${env[key].includes(value) ? "active" : ""}`}
                    onClick={() => updateEnv({ ...env, [key]: toggle(env[key], value) })}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
          ))}

          <div className="field">
            <label>Reuse mode</label>
            <select
              value={env.reuseMode}
              onChange={(e) =>
                updateEnv({
                  ...env,
                  reuseMode: e.target.value as Environment["reuseMode"],
                })
              }
            >
              <option value="preferCompatible">preferCompatible</option>
              <option value="exactReuse">exactReuse</option>
            </select>
          </div>

          {!localMode && (
            <>
              <h2>프로젝트</h2>
              {(
                [
                  ["repository", "Repository"],
                  ["gitRef", "Git ref"],
                  ["solutionPath", "Solution path"],
                  ["configuration", "Configuration"],
                  ["platform", "Platform"],
                ] as const
              ).map(([key, label]) => (
                <div className="field" key={key}>
                  <label>{label}</label>
                  <input
                    value={project[key]}
                    onChange={(e) => setProject({ ...project, [key]: e.target.value })}
                  />
                </div>
              ))}
            </>
          )}

          <div className="actions">
            <button className="primary" type="button" disabled={busy} onClick={onEnsure}>
              {localMode ? "Ensure image" : "1. Ensure image"}
            </button>
            {!localMode &&
              !imageReady &&
              ensureResult &&
              ["CREATING", "VALIDATING"].includes(ensureResult.imageStatus) &&
              options?.simulateWorkers && (
                <button
                  className="secondary"
                  type="button"
                  disabled={busy}
                  onClick={onSimulateFactory}
                >
                  Simulate factory
                </button>
              )}
            {!localMode && (
              <button
                className="primary"
                type="button"
                disabled={busy || !imageReady}
                onClick={onStartBuild}
              >
                2. Start build
              </button>
            )}
            {!localMode &&
              buildResult &&
              ![
                "SUCCEEDED",
                "PROFILE_REJECTED",
                "CANCELLED",
                "IMAGE_BUILD_FAILED",
                "PROJECT_BUILD_FAILED",
                "TEST_FAILED",
              ].includes(buildResult.status) &&
              options?.simulateWorkers && (
                <button
                  className="secondary"
                  type="button"
                  disabled={busy}
                  onClick={onSimulateBuild}
                >
                  Simulate project
                </button>
              )}
          </div>
          {error && <div className="error">{error}</div>}
        </section>

        <section className="panel">
          <h2>{localMode ? "결과" : "이미지 / 빌드"}</h2>
          {!ensureResult && !buildResult && (
            <p style={{ color: "var(--muted)", margin: 0 }}>
              Preset을 고르거나 구성 후 Ensure image를 실행하세요.
              {options ? ` Catalog ${options.catalogVersion}.` : ""}
            </p>
          )}

          {ensureResult && (
            <div className="result">
              <span
                className={`badge ${
                  ensureResult.ready
                    ? "ok"
                    : ensureResult.action === "REJECTED" || ensureResult.errorMessage
                      ? "err"
                      : "warn"
                }`}
              >
                {ensureResult.ready ? "READY" : ensureResult.imageStatus || ensureResult.action}
              </span>
              {ensureResult.matchType && (
                <div>
                  matchType: <span className="mono">{ensureResult.matchType}</span>
                </div>
              )}
              {ensureResult.matchedProfileHash && (
                <div>
                  profile: <span className="mono">{ensureResult.matchedProfileHash}</span>
                </div>
              )}
              {ensureResult.image && (
                <>
                  <div>
                    repository:{" "}
                    <span className="mono">
                      {ensureResult.image.repository}:{ensureResult.image.tag}
                    </span>
                  </div>
                  <div>
                    digest: <span className="mono">{ensureResult.image.digest}</span>
                  </div>
                </>
              )}
              {(ensureResult.localImageRef || ensureResult.localTarFile) && (
                <>
                  {ensureResult.localImageRef && (
                    <div>
                      local image: <span className="mono">{ensureResult.localImageRef}</span>
                    </div>
                  )}
                  {ensureResult.localTarFile && (
                    <div>
                      saved tar:{" "}
                      <span className="mono">./local-images/{ensureResult.localTarFile}</span>
                    </div>
                  )}
                  <p className="hint" style={{ marginTop: "0.75rem" }}>
                    확인:{" "}
                    <span className="mono">docker images msbuild-local</span> /{" "}
                    <span className="mono">
                      docker load -i ./local-images/{ensureResult.localTarFile || "&lt;tag&gt;.tar"}
                    </span>
                  </p>
                </>
              )}
              {!ensureResult.ready && ensureResult.estimatedWaitMinutes ? (
                <div>
                  {localMode ? "building…" : "estimated wait:"} ~{ensureResult.estimatedWaitMinutes}{" "}
                  min
                </div>
              ) : null}
              {ensureResult.errorMessage && (
                <div className="error">{ensureResult.errorMessage}</div>
              )}
            </div>
          )}

          {!localMode && buildResult && (
            <div className="result" style={{ marginTop: "1.25rem" }}>
              <h2>Build request</h2>
              <span
                className={`badge ${
                  ["BUILD_QUEUED", "SUCCEEDED"].includes(buildResult.status)
                    ? "ok"
                    : buildResult.status.includes("FAIL") || buildResult.status.includes("REJECT")
                      ? "err"
                      : "warn"
                }`}
              >
                {buildResult.status}
              </span>
              <div>
                id: <span className="mono">{buildResult.id}</span>
              </div>
              <div>
                matchType: <span className="mono">{buildResult.matchType}</span>
              </div>
              {buildResult.imageDigest && (
                <div>
                  digest: <span className="mono">{buildResult.imageDigest}</span>
                </div>
              )}
              {buildResult.errorMessage && <div className="error">{buildResult.errorMessage}</div>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
