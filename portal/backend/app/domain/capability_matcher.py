from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class MatchCandidate:
    profile_hash: str
    image_digest: str
    repository: str
    tag: str
    capability: dict[str, Any]
    windows_base: str
    vs_generation: str
    status: str
    hot: bool = False
    size_gib: float = 50.0
    cached_on_worker: bool = False


@dataclass
class MatchResult:
    match_type: str  # EXACT | COMPATIBLE_SUPERSET
    candidate: MatchCandidate
    provided_capabilities: list[str]
    extra_capabilities: list[str]
    score: float


def _as_version_set(values: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            result.add(str(value.get("version") or value.get("resolvedVersion")))
        else:
            result.add(str(value))
    return result


def capability_keys(capability: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    vs = capability.get("visualStudio")
    if vs:
        keys.add(f"vs{vs}")
    for fw in capability.get("dotnetFrameworks") or []:
        keys.add(f"net{str(fw).replace('.', '')}-targeting-pack")
    for sdk in _as_version_set(capability.get("dotnetSdks") or []):
        major_minor = ".".join(sdk.split(".")[:2])
        keys.add(f"dotnet-sdk-{major_minor}")
        keys.add(f"dotnet-sdk-{sdk}")
    for toolset in capability.get("cppToolsets") or []:
        keys.add(f"cpp-{toolset}")
    for sdk in capability.get("windowsSdks") or []:
        keys.add(f"windows-sdk-{sdk}")
    for feature in capability.get("features") or []:
        keys.add(str(feature))
    for custom in capability.get("customSdks") or []:
        if isinstance(custom, dict):
            keys.add(f"custom-{custom.get('name')}@{custom.get('version')}")
        else:
            keys.add(f"custom-{custom}")
    return keys


def _sdk_satisfied(requested: set[str], provided: set[str], policy: dict[str, Any]) -> bool:
    """requested holds exact resolved versions from Build Input capability_request."""
    approved = set(policy.get("approvedSdkRollForward") or [])
    for req in requested:
        if req in provided:
            continue
        # major.minor roll-forward only if explicitly approved
        major_minor = ".".join(req.split(".")[:2])
        if major_minor in approved:
            if any(p.startswith(major_minor + ".") for p in provided):
                continue
        return False
    return True


def is_compatible(
    requested: dict[str, Any],
    image_cap: dict[str, Any],
    *,
    matching_policy: dict[str, Any],
) -> bool:
    if matching_policy.get("visualStudioGeneration", "exact") == "exact":
        if str(requested.get("visualStudio")) != str(image_cap.get("visualStudio")):
            if not matching_policy.get("allowNewerVsForOlderProjects"):
                return False
            # optional newer-VS policy not implemented beyond flag gate
            return False

    if requested.get("windowsBase") and image_cap.get("windowsBase"):
        if requested["windowsBase"] != image_cap["windowsBase"]:
            return False

    req_fw = set(requested.get("dotnetFrameworks") or [])
    img_fw = set(image_cap.get("dotnetFrameworks") or [])
    if not req_fw.issubset(img_fw):
        return False

    req_sdk = _as_version_set(requested.get("dotnetSdks") or [])
    img_sdk = _as_version_set(image_cap.get("dotnetSdks") or [])
    if not _sdk_satisfied(req_sdk, img_sdk, matching_policy):
        return False

    # C++ toolset and Windows SDK: exact membership (no newer-implies-older)
    req_cpp = set(requested.get("cppToolsets") or [])
    img_cpp = set(image_cap.get("cppToolsets") or [])
    if not req_cpp.issubset(img_cpp):
        return False

    req_win = set(requested.get("windowsSdks") or [])
    img_win = set(image_cap.get("windowsSdks") or [])
    if not req_win.issubset(img_win):
        return False

    req_feat = set(requested.get("features") or [])
    img_feat = set(image_cap.get("features") or [])
    if not req_feat.issubset(img_feat):
        return False

    req_custom = set()
    for item in requested.get("customSdks") or []:
        if isinstance(item, dict):
            req_custom.add(f"{item.get('name')}@{item.get('version')}")
        else:
            req_custom.add(str(item))
    img_custom = set()
    for item in image_cap.get("customSdks") or []:
        if isinstance(item, dict):
            img_custom.add(f"{item.get('name')}@{item.get('version')}")
        else:
            img_custom.add(str(item))
    if not req_custom.issubset(img_custom):
        return False

    return True


def score_candidate(requested: dict[str, Any], candidate: MatchCandidate) -> float:
    req_keys = capability_keys(requested)
    img_keys = capability_keys(candidate.capability)
    extra = len(img_keys - req_keys)
    score = 100.0
    score -= extra * 2.0
    score -= candidate.size_gib * 0.1
    if candidate.status == "DEPRECATED":
        score -= 20.0
    if candidate.hot:
        score += 15.0
    if candidate.cached_on_worker:
        score += 10.0
    # prefer closer VS/feature exactness already guaranteed
    return score


def describe_capabilities(capability: dict[str, Any]) -> list[str]:
    return sorted(capability_keys(capability))


def match_images(
    *,
    requested_hash: str,
    capability_request: dict[str, Any],
    candidates: list[MatchCandidate],
    matching_policy: dict[str, Any],
    reuse_mode: str = "preferCompatible",
) -> MatchResult | None:
    # Exact first
    for candidate in candidates:
        if candidate.profile_hash == requested_hash and candidate.status in {"READY", "DEPRECATED"}:
            provided = describe_capabilities(candidate.capability)
            extra = sorted(set(provided) - set(describe_capabilities(capability_request)))
            return MatchResult(
                match_type="EXACT",
                candidate=candidate,
                provided_capabilities=provided,
                extra_capabilities=extra,
                score=10_000.0,
            )

    if reuse_mode == "exactReuse":
        return None

    scored: list[MatchResult] = []
    for candidate in candidates:
        if candidate.status not in {"READY", "DEPRECATED"}:
            continue
        if not is_compatible(capability_request, candidate.capability, matching_policy=matching_policy):
            continue
        provided = describe_capabilities(candidate.capability)
        req_desc = describe_capabilities(capability_request)
        extra = sorted(set(provided) - set(req_desc))
        scored.append(
            MatchResult(
                match_type="COMPATIBLE_SUPERSET",
                candidate=candidate,
                provided_capabilities=provided,
                extra_capabilities=extra,
                score=score_candidate(capability_request, candidate),
            )
        )

    if not scored:
        return None
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[0]