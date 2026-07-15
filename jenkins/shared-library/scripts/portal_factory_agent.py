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


def cmd_fetch(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/factory-artifacts"
    arts = _request("GET", url)
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
        hmac_secret=args.hmac_secret,
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
    print(json.dumps(_request("POST", url, body=body, hmac_secret=args.hmac_secret)))


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

    # Real host path (Windows): docker build with RO layout mounts.
    dockerfile = work / "Dockerfile"
    tag = f'{arts["stagingRepository"]}:{arts["imageTag"]}'
    build_cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        tag,
        str(work),
    ]
    print(json.dumps({"ok": False, "message": "non-dry-run requires Windows factory host", "cmd": build_cmd}))
    raise SystemExit(
        "Real docker build is host-specific. Re-run with --dry-run or set FACTORY_DRY_RUN=1 "
        "until Offline Layout mounts are configured."
    )


def _capability_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "visualStudio": manifest["visualStudio"]["generation"],
        "dotnetFrameworks": [
            # Prefer major.minor style when version looks like 4.8.1 -> keep as-is for measured;
            # Portal matcher for frameworks uses requested logical values; simulation uses request env.
            item.get("version") if isinstance(item, dict) else str(item)
            for item in manifest.get("dotnetFrameworkTargetingPacks") or []
        ],
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
    print(json.dumps(_request("POST", url, body=body, hmac_secret=args.hmac_secret)))


def cmd_fail(args: argparse.Namespace) -> None:
    url = f"{args.portal_url.rstrip('/')}/internal/v1/images/{args.profile_hash}/status"
    body = {
        "status": "FAILED",
        "leaseId": args.lease_id,
        "message": args.message or "Factory failed",
    }
    print(json.dumps(_request("POST", url, body=body, hmac_secret=args.hmac_secret)))


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
        if need_hmac:
            p.add_argument("--hmac-secret", required=True)

    p_fetch = sub.add_parser("fetch")
    add_common(p_fetch)

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
