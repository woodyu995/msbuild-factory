#!/usr/bin/env python3
"""Portal Image Factory agent used by Jenkins / factory hosts.

Commands:
  fetch | heartbeat | build | finalize | fail | dry-run-all
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _request(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    hmac_secret: str | None = None,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    raw = b""
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        data = raw
        headers["Content-Type"] = "application/json"
    if hmac_secret is not None:
        ts = str(int(time.time()))
        headers["X-Timestamp"] = ts
        headers["X-Signature"] = _sign(hmac_secret, ts, raw)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {url}: {detail}") from exc


def _resolve_hmac_secret(args: argparse.Namespace) -> str:
    secret = (
        getattr(args, "hmac_secret", None)
        or os.environ.get("PORTAL_HMAC_SECRET")
        or os.environ.get("PORTAL_CALLBACK_HMAC_SECRET")
    )
    if not secret:
        raise SystemExit(
            "HMAC secret required via --hmac-secret or PORTAL_HMAC_SECRET env"
        )
    return secret


def cmd_fetch(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/factory-artifacts"
    arts = _request(
        "POST",
        url,
        body={"leaseId": args.lease_id},
        hmac_secret=_resolve_hmac_secret(args),
    )
    (work / "Dockerfile").write_text(arts["dockerfile"], encoding="utf-8")
    (work / "profile.vsconfig").write_text(
        json.dumps(arts["vsconfig"], indent=2), encoding="utf-8"
    )
    (work / "install-manifest.json").write_text(
        json.dumps(arts["installManifest"], indent=2), encoding="utf-8"
    )
    (work / "artifacts.json").write_text(json.dumps(arts, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "workDir": str(work), "imageTag": arts.get("imageTag")}))


def cmd_heartbeat(args: argparse.Namespace) -> None:
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/heartbeat"
    result = _request(
        "POST",
        url,
        body={"leaseId": args.lease_id},
        hmac_secret=_resolve_hmac_secret(args),
    )
    print(json.dumps(result))


def cmd_event(args: argparse.Namespace, event_type: str, message: str) -> None:
    if not args.request_id:
        return
    url = f"{args.portal_url.rstrip('/')}/internal/v1/build-events"
    body = {
        "requestId": args.request_id,
        "eventType": event_type,
        "message": message,
    }
    print(json.dumps(_request("POST", url, body=body, hmac_secret=_resolve_hmac_secret(args))))


def cmd_build(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    arts = json.loads((work / "artifacts.json").read_text(encoding="utf-8"))
    dry_run = args.dry_run or os.environ.get("FACTORY_DRY_RUN", "").lower() in {"1", "true", "yes"}

    cmd_event(args, "IMAGE_BUILDING", "Factory build started")
    cmd_heartbeat(args)

    if dry_run:
        digest = f"sha256:dry-run-{args.profile_hash[:24]}"
        capability = _capability_from_manifest(arts["installManifest"])
        (work / "result.json").write_text(
            json.dumps(
                {
                    "dryRun": True,
                    "imageDigest": digest,
                    "capabilityProfile": capability,
                    "stagingRepository": arts.get("stagingRepository"),
                    "finalRepository": arts.get("finalRepository"),
                    "imageTag": arts.get("imageTag"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"ok": True, "dryRun": True, "imageDigest": digest}))
        return

    import shutil
    import subprocess

    if shutil.which("docker") is None:
        raise SystemExit("docker not found on PATH; use --dry-run or install Docker Engine")

    layout_root = args.layout_root or os.environ.get("IMAGE_FACTORY_LAYOUT_ROOT", "")
    installer_root = args.installer_root or os.environ.get("IMAGE_FACTORY_INSTALLER_ROOT", "")
    if not layout_root or not installer_root:
        raise SystemExit(
            "IMAGE_FACTORY_LAYOUT_ROOT and IMAGE_FACTORY_INSTALLER_ROOT are required for real builds"
        )

    # Copy shared install scripts into build context if present beside this repo layout.
    scripts_src = Path(__file__).resolve().parents[3] / "portal" / "backend" / "image_factory" / "scripts"
    scripts_dst = work / "scripts"
    if scripts_src.exists() and not scripts_dst.exists():
        shutil.copytree(scripts_src, scripts_dst)

    staging_tag = f'{arts["stagingRepository"]}:{arts["imageTag"]}'
    final_tag = f'{arts["finalRepository"]}:{arts["imageTag"]}'
    dockerfile = work / "Dockerfile"

    build_cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        staging_tag,
        str(work),
    ]
    # RO mounts for layout/installers are host-engine specific; export as build-arg paths.
    env = os.environ.copy()
    env["IMAGE_FACTORY_LAYOUT_ROOT"] = layout_root
    env["IMAGE_FACTORY_INSTALLER_ROOT"] = installer_root

    print(json.dumps({"ok": True, "phase": "docker-build", "cmd": build_cmd}))
    built = subprocess.run(build_cmd, check=False, capture_output=True, text=True, env=env)
    if built.returncode != 0:
        raise SystemExit(f"docker build failed: {built.stderr or built.stdout}")

    # Promote staging -> final tag locally, login to Nexus if configured, then push.
    subprocess.run(["docker", "tag", staging_tag, final_tag], check=True)

    nexus_user = os.environ.get("NEXUS_DOCKER_USER") or os.environ.get("REGISTRY_USER")
    nexus_pass = os.environ.get("NEXUS_DOCKER_PASSWORD") or os.environ.get("REGISTRY_PASSWORD")
    registry_host = (arts.get("registryHost") or os.environ.get("NEXUS_REGISTRY_HOST") or "").strip()
    if nexus_user and nexus_pass and registry_host:
        login = subprocess.run(
            ["docker", "login", registry_host, "-u", nexus_user, "--password-stdin"],
            input=nexus_pass,
            check=False,
            capture_output=True,
            text=True,
        )
        if login.returncode != 0:
            raise SystemExit(f"docker login to Nexus failed: {login.stderr or login.stdout}")

    # Also push staging (optional audit trail) then final.
    if os.environ.get("FACTORY_PUSH_STAGING", "").lower() in {"1", "true", "yes"}:
        subprocess.run(["docker", "push", staging_tag], check=False)
    push = subprocess.run(["docker", "push", final_tag], check=False, capture_output=True, text=True)
    if push.returncode != 0:
        raise SystemExit(f"docker push to Nexus failed: {push.stderr or push.stdout}")

    digest = ""
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", final_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0 and inspect.stdout.strip():
        try:
            digests = json.loads(inspect.stdout)
            for entry in digests or []:
                if "@sha256:" in entry:
                    digest = "sha256:" + entry.split("@sha256:", 1)[1].strip()
                    break
        except json.JSONDecodeError:
            digest = ""
    if not digest:
        inspect2 = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", final_tag],
            check=True,
            capture_output=True,
            text=True,
        )
        digest = inspect2.stdout.strip()
        if not digest.startswith("sha256:"):
            digest = f"sha256:{digest}"

    capability = _capability_from_manifest(arts["installManifest"])
    (work / "result.json").write_text(
        json.dumps(
            {
                "dryRun": False,
                "imageDigest": digest,
                "capabilityProfile": capability,
                "stagingRepository": arts.get("stagingRepository"),
                "finalRepository": arts.get("finalRepository"),
                "imageTag": arts.get("imageTag"),
                "finalTag": final_tag,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "dryRun": False, "imageDigest": digest, "finalTag": final_tag}))


def _netfx_logical_version(resolved_version: str) -> str:
    """Align measured capability with Portal matcher logical framework ids."""
    if resolved_version.startswith("4.8."):
        return "4.8"
    if resolved_version == "4.6.0":
        return "4.6"
    return resolved_version


def _capability_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    # Prefer explicit logical list from Portal installManifest (matcher ⊆ contract).
    if manifest.get("dotnetFrameworks"):
        frameworks = [str(v) for v in manifest["dotnetFrameworks"]]
    else:
        frameworks = [
            _netfx_logical_version(
                str(item.get("version") if isinstance(item, dict) else item)
            )
            for item in manifest.get("dotnetFrameworkTargetingPacks") or []
        ]
    return {
        "visualStudio": manifest["visualStudio"]["generation"],
        "dotnetFrameworks": frameworks,
        "dotnetSdks": [
            {"version": item["version"]} if isinstance(item, dict) else {"version": str(item)}
            for item in manifest.get("dotnetSdks") or []
        ],
        "cppToolsets": list(manifest.get("cppToolsets") or []),
        "windowsSdks": [
            item["version"] if isinstance(item, dict) else str(item)
            for item in manifest.get("windowsSdks") or []
        ],
        "features": list(manifest.get("features") or []),
        "windowsBase": manifest["windowsBase"]["name"],
        "customSdks": list(manifest.get("customSdks") or []),
    }


def cmd_finalize(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    result_path = work / "result.json"
    if not result_path.exists():
        raise SystemExit("result.json missing; run build first")
    result = json.loads(result_path.read_text(encoding="utf-8"))

    cmd_event(args, "IMAGE_VALIDATING", "Factory validating image")
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/status"
    body = {
        "status": "READY",
        "leaseId": args.lease_id,
        "message": "Factory finalize completed",
        "imageDigest": result["imageDigest"],
        "capabilityProfile": result["capabilityProfile"],
        "requestId": args.request_id,
    }
    print(json.dumps(_request("POST", url, body=body, hmac_secret=_resolve_hmac_secret(args))))


def cmd_fail(args: argparse.Namespace) -> None:
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/status"
    body = {
        "status": "FAILED",
        "leaseId": args.lease_id,
        "message": args.message or "Factory failed",
    }
    print(json.dumps(_request("POST", url, body=body, hmac_secret=_resolve_hmac_secret(args))))


def cmd_dry_run_all(args: argparse.Namespace) -> None:
    args.dry_run = True
    cmd_fetch(args)
    cmd_heartbeat(args)
    cmd_build(args)
    cmd_finalize(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portal Image Factory agent")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, need_hmac: bool = False) -> None:
        p.add_argument("--portal-url", required=True)
        p.add_argument("--profile-hash", required=True)
        p.add_argument("--lease-id", required=True)
        p.add_argument("--request-id", default="")
        p.add_argument("--work-dir", default="./factory-work")
        # Optional CLI; falls back to PORTAL_HMAC_SECRET env (preferred on hosts).
        if need_hmac:
            p.add_argument("--hmac-secret", default="")

    p_fetch = sub.add_parser("fetch")
    add_common(p_fetch, need_hmac=True)

    p_hb = sub.add_parser("heartbeat")
    add_common(p_hb, need_hmac=True)

    p_build = sub.add_parser("build")
    add_common(p_build, need_hmac=True)
    p_build.add_argument("--layout-root", default="")
    p_build.add_argument("--installer-root", default="")
    p_build.add_argument("--dry-run", action="store_true")

    p_fin = sub.add_parser("finalize")
    add_common(p_fin, need_hmac=True)

    p_fail = sub.add_parser("fail")
    add_common(p_fail, need_hmac=True)
    p_fail.add_argument("--message", default="Factory failed")

    p_all = sub.add_parser("dry-run-all")
    add_common(p_all, need_hmac=True)
    p_all.add_argument("--dry-run", action="store_true", default=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "fetch": cmd_fetch,
        "heartbeat": cmd_heartbeat,
        "build": cmd_build,
        "finalize": cmd_finalize,
        "fail": cmd_fail,
        "dry-run-all": cmd_dry_run_all,
    }
    commands[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
