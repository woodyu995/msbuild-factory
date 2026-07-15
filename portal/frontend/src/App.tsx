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
};

type ValidateResult = {
  valid: boolean;
  requestedProfileHash?: string;
  matchedProfileHash?: string;
  matchType?: string;
  action: string;
  estimatedWaitMinutes?: number;
  providedCapabilities?: string[];
  extraCapabilities?: string[];
  errorMessage?: string;
  image?: { repository: string; tag: string; digest: string };
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

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
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
  const [validateResult, setValidateResult] = useState<ValidateResult | null>(null);
  const [buildResult, setBuildResult] = useState<BuildRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/v1/build-environment/options")
      .then((r) => r.json())
      .then(setOptions)
      .catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (!buildResult?.id) return;
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
      fetch(`/api/v1/build-requests/${buildResult.id}`)
        .then((r) => r.json())
        .then((data) => setBuildResult(data))
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [buildResult?.id, buildResult?.status]);

  const vs = useMemo(
    () => options?.visualStudios.find((item) => item.id === env.visualStudio),
    [options, env.visualStudio],
  );

  async function onValidate() {
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/v1/build-environment/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ environment: env }),
      });
      const data = await resp.json();
      setValidateResult(data);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit() {
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/v1/build-requests", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "X-Actor": "portal-ui",
        },
        body: JSON.stringify({
          project,
          environment: env,
          nuget: { mode: "repo-packages-and-internal-feed" },
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(data?.detail?.message || JSON.stringify(data));
        return;
      }
      setBuildResult(data);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSimulate() {
    if (!buildResult?.id) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(`/api/v1/build-requests/${buildResult.id}/simulate`, {
        method: "POST",
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(data?.detail || JSON.stringify(data));
        return;
      }
      const refreshed = await fetch(`/api/v1/build-requests/${buildResult.id}`);
      setBuildResult(await refreshed.json());
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
          승인된 빌드 도구를 선택하면 Exact 또는 Capability Superset 이미지로
          매칭합니다. Factory/Project Worker가 없으면 Simulate로 로컬 완료 경로를
          실행할 수 있습니다.
          {options?.simulateWorkers ? " (auto-simulate ON)" : ""}
        </p>
      </header>

      <div className="grid">
        <section className="panel">
          <h2>빌드 환경</h2>
          {options && (
            <div className="presets">
              {options.presets.map((preset) => (
                <button
                  key={preset.id}
                  className="preset"
                  type="button"
                  onClick={() =>
                    setEnv({
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
                setEnv({
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
                    onClick={() => setEnv({ ...env, [key]: toggle(env[key], value) })}
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
                setEnv({
                  ...env,
                  reuseMode: e.target.value as Environment["reuseMode"],
                })
              }
            >
              <option value="preferCompatible">preferCompatible</option>
              <option value="exactReuse">exactReuse</option>
            </select>
          </div>

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

          <div className="actions">
            <button className="secondary" type="button" disabled={busy} onClick={onValidate}>
              Validate / Match
            </button>
            <button className="primary" type="button" disabled={busy} onClick={onSubmit}>
              Submit build request
            </button>
            {buildResult &&
              !["SUCCEEDED", "PROFILE_REJECTED", "CANCELLED", "IMAGE_BUILD_FAILED", "PROJECT_BUILD_FAILED", "TEST_FAILED"].includes(
                buildResult.status,
              ) && (
                <button className="secondary" type="button" disabled={busy} onClick={onSimulate}>
                  Simulate workers
                </button>
              )}
          </div>
          {error && <div className="error">{error}</div>}
        </section>

        <section className="panel">
          <h2>매칭 결과</h2>
          {!validateResult && !buildResult && (
            <p style={{ color: "var(--muted)", margin: 0 }}>
              Preset을 고르거나 구성 후 Validate를 실행하세요.
              {options ? ` Catalog ${options.catalogVersion}.` : ""}
            </p>
          )}

          {validateResult && (
            <div className="result">
              <span
                className={`badge ${
                  validateResult.valid ? "ok" : validateResult.action === "REJECTED" ? "err" : "warn"
                }`}
              >
                {validateResult.action}
              </span>
              {validateResult.matchType && (
                <div>
                  matchType: <span className="mono">{validateResult.matchType}</span>
                </div>
              )}
              {validateResult.requestedProfileHash && (
                <div>
                  requested: <span className="mono">{validateResult.requestedProfileHash}</span>
                </div>
              )}
              {validateResult.matchedProfileHash && (
                <div>
                  matched: <span className="mono">{validateResult.matchedProfileHash}</span>
                </div>
              )}
              {validateResult.image && (
                <div>
                  digest: <span className="mono">{validateResult.image.digest}</span>
                </div>
              )}
              {!!validateResult.extraCapabilities?.length && (
                <div>
                  extra: <span className="mono">{validateResult.extraCapabilities.join(", ")}</span>
                </div>
              )}
              {validateResult.errorMessage && (
                <div className="error">{validateResult.errorMessage}</div>
              )}
            </div>
          )}

          {buildResult && (
            <div className="result" style={{ marginTop: "1.25rem" }}>
              <h2>Build request</h2>
              <span
                className={`badge ${
                  ["BUILD_QUEUED", "SUCCEEDED"].includes(buildResult.status)
                    ? "ok"
                    : ["IMAGE_BUILD_QUEUED", "IMAGE_WAITING", "IMAGE_BUILDING", "IMAGE_VALIDATING"].includes(
                          buildResult.status,
                        )
                      ? "warn"
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