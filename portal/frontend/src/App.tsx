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

function apiHeaders(extra: Record<string, string> = {}, token: string): HeadersInit {
  const headers: Record<string, string> = { ...extra };
  if (token) {
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
  const [apiToken, setApiToken] = useState(loadToken);
  const [ensureResult, setEnsureResult] = useState<EnsureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const localMode = Boolean(options?.localFactory);
  const showAuth = Boolean(options?.requireAuth);

  function updateEnv(next: Environment) {
    setEnv(next);
    setEnsureResult(null);
    setError(null);
  }

  useEffect(() => {
    if (showAuth) {
      localStorage.setItem(TOKEN_KEY, apiToken);
    }
  }, [apiToken, showAuth]);

  useEffect(() => {
    fetch("/api/v1/build-environment/options", {
      headers: apiHeaders({}, apiToken),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text());
        return r.json();
      })
      .then(setOptions)
      .catch((err) => setError(String(err)));
  }, [apiToken]);

  useEffect(() => {
    const hash = ensureResult?.matchedProfileHash;
    if (!hash || ensureResult?.ready) return;
    if (!["CREATING", "VALIDATING"].includes(ensureResult?.imageStatus || "")) return;

    const timer = window.setInterval(() => {
      fetch(`/api/v1/images/${hash}`, {
        headers: apiHeaders({}, apiToken),
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
  ]);

  const vs = useMemo(
    () => options?.visualStudios.find((item) => item.id === env.visualStudio),
    [options, env.visualStudio],
  );

  async function onEnsure() {
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/v1/images/ensure", {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }, apiToken),
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

  return (
    <div className="app">
      <header className="brand">
        <h1>MSBuild Build Portal</h1>
        <p>
          {localMode
            ? "환경을 고르고 Ensure image를 누르면 로컬에 이미지가 빌드·저장됩니다. (로그인/Nexus/Jenkins 없음)"
            : "환경을 고른 뒤 Ensure image로 READY 이미지 digest를 확보합니다."}
          {options?.simulateWorkers ? " · simulate ON" : ""}
        </p>
      </header>

      <div className="grid">
        <section className="panel">
          <h2>빌드 환경</h2>
          {showAuth && (
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

          <div className="actions">
            <button className="primary" type="button" disabled={busy} onClick={onEnsure}>
              Ensure image
            </button>
          </div>
          {error && <div className="error">{error}</div>}
        </section>

        <section className="panel">
          <h2>결과</h2>
          {!ensureResult && (
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
                <div>building… (~{ensureResult.estimatedWaitMinutes} min estimate)</div>
              ) : null}
              {ensureResult.errorMessage && (
                <div className="error">{ensureResult.errorMessage}</div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
