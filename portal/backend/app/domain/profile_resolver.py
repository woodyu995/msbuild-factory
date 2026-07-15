from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.catalog import Catalog
from app.domain.profile_hash import canonical_json, profile_hash


class ProfileRejected(Exception):
    def __init__(self, message: str, *, code: str = "PROFILE_REJECTED"):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ResolvedProfile:
    requested: dict[str, Any]
    build_input: dict[str, Any]
    capability_request: dict[str, Any]
    profile_hash: str
    canonical_json: str
    windows_base: str
    vs_generation: str


def _uniq_sorted(values: list[str] | None) -> list[str]:
    return sorted({v for v in (values or []) if v})


def _rule_matches(when: dict[str, Any], env: dict[str, Any]) -> bool:
    if "visualStudio" in when and env.get("visualStudio") != when["visualStudio"]:
        return False
    if "featuresIncludes" in when:
        features = set(env.get("features") or [])
        needed = set(when["featuresIncludes"] or [])
        if not needed.issubset(features):
            return False
    return True


def validate_environment(catalog: Catalog, environment: dict[str, Any]) -> None:
    vs = environment.get("visualStudio")
    if not vs:
        raise ProfileRejected("visualStudio is required")
    entry = catalog.vs_entry(str(vs))
    allowed = entry["allowed"]

    def check_list(field: str, values: list[str]) -> None:
        allowed_values = set(allowed.get(field, []))
        unknown = [v for v in values if v not in allowed_values]
        if unknown:
            raise ProfileRejected(f"{field} not allowed for VS{vs}: {unknown}")

    frameworks = _uniq_sorted(environment.get("dotnetFrameworks"))
    sdks = _uniq_sorted(environment.get("dotnetSdks"))
    toolsets = _uniq_sorted(environment.get("cppToolsets"))
    winsdks = _uniq_sorted(environment.get("windowsSdks"))
    features = _uniq_sorted(environment.get("features"))

    check_list("dotnetFrameworks", frameworks)
    check_list("dotnetSdks", sdks)
    check_list("cppToolsets", toolsets)
    check_list("windowsSdks", winsdks)
    check_list("features", features)

    normalized_env = {
        "visualStudio": str(vs),
        "dotnetFrameworks": frameworks,
        "dotnetSdks": sdks,
        "cppToolsets": toolsets,
        "windowsSdks": winsdks,
        "features": features,
    }

    for rule in catalog.compatibility_rules:
        when = rule.get("when") or {}
        if not _rule_matches(when, normalized_env):
            continue
        deny = rule.get("deny") or {}
        for field, banned in deny.items():
            overlap = set(normalized_env.get(field) or []) & set(banned or [])
            if overlap:
                raise ProfileRejected(f"compatibility deny: {field}={sorted(overlap)}")
        require_any = rule.get("requireAny") or {}
        for field, options in require_any.items():
            if not set(normalized_env.get(field) or []) & set(options or []):
                raise ProfileRejected(f"compatibility requireAny failed for {field}: need one of {options}")


def _resolve_installer(catalog: Catalog, key: str) -> dict[str, str]:
    installers = catalog.installers
    if key not in installers:
        raise ProfileRejected(f"installer mapping missing for {key}")
    item = installers[key]
    return {
        "version": str(item["resolvedVersion"]),
        "installerSha256": str(item["installerSha256"]),
    }


def _component_ids(entry: dict[str, Any], environment: dict[str, Any]) -> list[str]:
    ids = entry.get("componentIds") or {}
    selected: list[str] = []
    if "msbuild" in ids:
        selected.append(ids["msbuild"])
    for feature in environment["features"]:
        if feature in ids:
            selected.append(ids[feature])
    for toolset in environment["cppToolsets"]:
        key = f"cpp-{toolset}"
        if key in ids:
            selected.append(ids[key])
    sdk_map = {
        "10.0.19041.0": "windows-sdk-19041",
        "10.0.22621.0": "windows-sdk-22621",
    }
    for winsdk in environment["windowsSdks"]:
        key = sdk_map.get(winsdk)
        if key and key in ids:
            selected.append(ids[key])
    # stable unique
    return sorted(set(selected))


def resolve_profile(catalog: Catalog, environment: dict[str, Any]) -> ResolvedProfile:
    validate_environment(catalog, environment)

    vs = str(environment["visualStudio"])
    entry = catalog.vs_entry(vs)
    windows_base = str(entry["windowsBase"])

    requested = {
        "visualStudio": vs,
        "dotnetFrameworks": _uniq_sorted(environment.get("dotnetFrameworks")),
        "dotnetSdks": _uniq_sorted(environment.get("dotnetSdks")),
        "cppToolsets": _uniq_sorted(environment.get("cppToolsets")),
        "windowsSdks": _uniq_sorted(environment.get("windowsSdks")),
        "features": _uniq_sorted(environment.get("features")),
        "reuseMode": environment.get("reuseMode") or "preferCompatible",
    }

    if windows_base not in catalog.compatible_workers:
        raise ProfileRejected(
            f"no compatible Windows worker for {windows_base}",
            code="NO_COMPATIBLE_WORKER",
        )

    agent = catalog.raw["agentBases"][windows_base]
    base = catalog.raw["baseImages"][windows_base]

    build_input = {
        "schemaVersion": 2,
        "windowsBase": {"name": windows_base, "digest": base["digest"]},
        "agentBase": {"image": agent["image"], "digest": agent["digest"]},
        "visualStudio": {
            "generation": vs,
            "layoutRelease": entry["layoutRelease"],
            "layoutManifestDigest": entry["layoutManifestDigest"],
            "components": _component_ids(entry, requested),
        },
        "dotnetFrameworkTargetingPacks": [
            _resolve_installer(catalog, fw) for fw in requested["dotnetFrameworks"]
        ],
        "dotnetSdks": [_resolve_installer(catalog, sdk) for sdk in requested["dotnetSdks"]],
        "cppToolsets": requested["cppToolsets"],
        "windowsSdks": [
            {
                "version": _resolve_installer(catalog, w)["version"],
                "installerSha256": _resolve_installer(catalog, w)["installerSha256"],
            }
            for w in requested["windowsSdks"]
        ],
        "features": requested["features"],
        "customSdks": [],
        "templateVersion": catalog.raw["templateVersion"],
        "installScriptVersion": catalog.raw["installScriptVersion"],
        "validationSuiteVersion": catalog.raw["validationSuiteVersion"],
    }

    # Keep installer lists sorted by version for canonical stability
    build_input["dotnetFrameworkTargetingPacks"] = sorted(
        build_input["dotnetFrameworkTargetingPacks"], key=lambda x: x["version"]
    )
    build_input["dotnetSdks"] = sorted(build_input["dotnetSdks"], key=lambda x: x["version"])
    build_input["windowsSdks"] = sorted(build_input["windowsSdks"], key=lambda x: x["version"])

    capability_request = {
        "visualStudio": vs,
        "dotnetFrameworks": list(requested["dotnetFrameworks"]),
        "dotnetSdks": [item["version"] for item in build_input["dotnetSdks"]],
        "cppToolsets": list(requested["cppToolsets"]),
        "windowsSdks": [item["version"] for item in build_input["windowsSdks"]],
        "features": list(requested["features"]),
        "windowsBase": windows_base,
        "customSdks": [],
    }

    digest = profile_hash(build_input)
    return ResolvedProfile(
        requested=requested,
        build_input=build_input,
        capability_request=capability_request,
        profile_hash=digest,
        canonical_json=canonical_json(build_input),
        windows_base=windows_base,
        vs_generation=vs,
    )