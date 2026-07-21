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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class _HeartbeatKeeper:
    """Renew Portal factory lease while a long docker/robocopy build runs."""

    def __init__(self, args: argparse.Namespace, *, interval_sec: int | None = None) -> None:
        raw = os.environ.get("FACTORY_HEARTBEAT_INTERVAL_SEC", "").strip()
        if interval_sec is not None:
            self.interval_sec = max(30, interval_sec)
        elif raw:
            self.interval_sec = max(30, int(raw))
        else:
            self.interval_sec = 600
        self.args = args
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _HeartbeatKeeper:
        try:
            cmd_heartbeat(self.args)
        except SystemExit as exc:
            print(json.dumps({"ok": False, "phase": "heartbeat", "error": str(exc)}))
        self._thread = threading.Thread(target=self._loop, name="factory-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            cmd_heartbeat(self.args)
        except SystemExit:
            pass

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                cmd_heartbeat(self.args)
            except SystemExit as exc:
                print(json.dumps({"ok": False, "phase": "heartbeat", "error": str(exc)}))
            except Exception as exc:  # noqa: BLE001 — keep build alive if Portal blips
                print(json.dumps({"ok": False, "phase": "heartbeat", "error": str(exc)}))


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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _docker_supports_build_context() -> bool:
    """True when `docker build` accepts named `--build-context` (BuildKit / recent Docker)."""
    import subprocess

    help_run = subprocess.run(
        ["docker", "build", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    text = f"{help_run.stdout or ''}{help_run.stderr or ''}"
    return "--build-context" in text


def _rm_tree(path: Path) -> None:
    """Remove a file/dir/junction (Windows junctions need rmdir, not unlink of target)."""
    import shutil
    import subprocess

    if not path.exists() and not path.is_symlink():
        # Junction may still report exists() inconsistently; try rmdir anyway on Windows.
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "rmdir", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        return
    if os.name == "nt":
        # Prefer rmdir for junctions/symlinks so the target is not deleted.
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if removed.returncode == 0 or not path.exists():
            return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _embed_dir_into_context(src: Path, dst: Path) -> None:
    """Fully copy src into the Docker build context.

    Windows Docker often omits junctions/symlinks from the default build context,
    so named `--build-context` is preferred when available. Older Docker lacks that
    flag — fall back to a real robocopy/shutil copy (slow for multi‑GB VS layouts).
    """
    import shutil
    import subprocess

    if not src.exists():
        raise SystemExit(f"required path missing: {src}")
    if dst.exists() or dst.is_symlink():
        _rm_tree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # robocopy exit codes 0–7 are success (bit flags); >= 8 is failure.
        copied = subprocess.run(
            [
                "robocopy",
                str(src),
                str(dst),
                "/E",
                "/NFL",
                "/NDL",
                "/NJH",
                "/NJS",
                "/nc",
                "/ns",
                "/np",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if copied.returncode >= 8:
            raise SystemExit(
                f"robocopy failed ({src} -> {dst}, code={copied.returncode}): "
                f"{copied.stderr or copied.stdout}"
            )
        return
    shutil.copytree(src, dst)


def _rewrite_dockerfile_copy_mode(df_text: str, *, use_build_context: bool) -> str:
    """Switch between named-context COPY and in-context COPY layout/installers."""
    if use_build_context:
        out = df_text
        out = out.replace("COPY layout C:\\Layout", "COPY --from=layout . C:\\Layout")
        out = out.replace("COPY installers C:\\Installers", "COPY --from=installers . C:\\Installers")
        if "COPY --from=layout" in out and not out.lstrip().startswith("# syntax="):
            out = "# syntax=docker/dockerfile:1.4\n" + out
        return out
    out = df_text
    out = out.replace("COPY --from=layout . C:\\Layout", "COPY layout C:\\Layout")
    out = out.replace("COPY --from=installers . C:\\Installers", "COPY installers C:\\Installers")
    return out


def _resolve_layout_dir(layout_root: str, layout_release: str) -> Path:
    root = Path(layout_root)
    candidates = [root / layout_release, root]
    for cand in candidates:
        for name in ("vs_setup.exe", "vs_BuildTools.exe"):
            if (cand / name).exists():
                return cand
    # Still allow the release folder even if setup name differs — install script will fail clearly.
    if (root / layout_release).exists():
        return root / layout_release
    return root


def cmd_build(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    arts = json.loads((work / "artifacts.json").read_text(encoding="utf-8"))
    dry_run = args.dry_run or _env_flag("FACTORY_DRY_RUN")

    cmd_event(args, "IMAGE_BUILDING", "Factory build started")

    if dry_run:
        cmd_heartbeat(args)
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

    # Heartbeat for the whole embed + docker build window (can be many hours).
    with _HeartbeatKeeper(args):
        # Copy shared install scripts into build context if present beside this repo layout.
        scripts_src = Path(__file__).resolve().parents[3] / "portal" / "backend" / "image_factory" / "scripts"
        scripts_dst = work / "scripts"
        if scripts_src.exists():
            if scripts_dst.exists():
                shutil.rmtree(scripts_dst)
            shutil.copytree(scripts_src, scripts_dst)
        elif not scripts_dst.exists():
            raise SystemExit(
                f"install scripts missing: expected {scripts_src} or {scripts_dst}"
            )

        layout_release = str(
            (arts.get("installManifest") or {}).get("visualStudio", {}).get("layoutRelease") or ""
        )
        layout_src = _resolve_layout_dir(layout_root, layout_release)
        if not layout_src.exists():
            raise SystemExit(f"layout path missing: {layout_src}")

        installer_src = Path(installer_root)
        if not installer_src.exists():
            installer_src.mkdir(parents=True, exist_ok=True)
            (installer_src / ".keep").write_text("", encoding="utf-8")

        # Prefer Docker named build-contexts when the daemon supports them. Older
        # Windows Docker rejects `--build-context`; then fully embed layout/installers
        # into the work dir (junctions are invisible to Windows docker build).
        force_embed = _env_flag("FACTORY_EMBED_LAYOUT_IN_CONTEXT")
        supports_build_context = _docker_supports_build_context()
        use_build_context = supports_build_context and not force_embed
        if not use_build_context:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "phase": "embed-layout",
                        "reason": (
                            "FACTORY_EMBED_LAYOUT_IN_CONTEXT"
                            if force_embed
                            else "docker-build lacks --build-context"
                        ),
                        "layoutSrc": str(layout_src),
                        "installerSrc": str(installer_src),
                        "note": "Full copy into work dir — may take a long time for VS layouts",
                    }
                )
            )
            _embed_dir_into_context(layout_src, work / "layout")
            _embed_dir_into_context(installer_src, work / "installers")

        staging_tag = f'{arts["stagingRepository"]}:{arts["imageTag"]}'
        final_tag = f'{arts["finalRepository"]}:{arts["imageTag"]}'
        dockerfile = work / "Dockerfile"

        df_text = _rewrite_dockerfile_copy_mode(
            dockerfile.read_text(encoding="utf-8"),
            use_build_context=use_build_context,
        )
        dockerfile.write_text(df_text, encoding="utf-8")

        build_cmd = [
            "docker",
            "build",
            "-f",
            str(dockerfile),
            "-t",
            staging_tag,
        ]
        if _env_flag("FACTORY_DOCKER_BUILD_NO_CACHE"):
            build_cmd.append("--no-cache")
        if use_build_context:
            build_cmd.extend(
                [
                    "--build-context",
                    f"layout={layout_src}",
                    "--build-context",
                    f"installers={installer_src}",
                ]
            )
        build_cmd.append(str(work))
        env = os.environ.copy()
        env["IMAGE_FACTORY_LAYOUT_ROOT"] = str(layout_src)
        env["IMAGE_FACTORY_INSTALLER_ROOT"] = str(installer_src)

        print(
            json.dumps(
                {
                    "ok": True,
                    "phase": "docker-build",
                    "cmd": build_cmd,
                    "layoutSrc": str(layout_src),
                    "supportsBuildContext": supports_build_context,
                    "useBuildContext": use_build_context,
                }
            )
        )
        # Stream docker build so VS installer errors are visible; also keep a log file.
        # Windows docker/console often emits non-UTF8 bytes → never use bare text=True.
        log_path = work / "docker-build.log"
        print(json.dumps({"ok": True, "phase": "docker-build-log", "path": str(log_path)}))
        with log_path.open("w", encoding="utf-8", errors="replace") as log_f:
            built = subprocess.run(
                build_cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            out = built.stdout or ""
            log_f.write(out)
            # Print last chunk to console (full log is in docker-build.log).
            tail = out[-12000:] if len(out) > 12000 else out
            if tail:
                print(tail)
        if built.returncode != 0:
            raise SystemExit(
                f"docker build failed (exit {built.returncode}); see {log_path}"
            )

        # Promote staging -> final tag locally.
        subprocess.run(["docker", "tag", staging_tag, final_tag], check=True)

        skip_push = _env_flag("FACTORY_SKIP_PUSH")
        local_tar = None
        if skip_push:
            save_dir = os.environ.get("FACTORY_DOCKER_SAVE_DIR", "").strip()
            if save_dir:
                out = Path(save_dir)
                out.mkdir(parents=True, exist_ok=True)
                local_tar = out / f'{arts["imageTag"]}.tar'
                saved = subprocess.run(
                    ["docker", "save", "-o", str(local_tar), final_tag],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if saved.returncode != 0:
                    raise SystemExit(f"docker save failed: {saved.stderr or saved.stdout}")
                print(json.dumps({"ok": True, "phase": "docker-save", "tar": str(local_tar)}))
        else:
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

            if _env_flag("FACTORY_PUSH_STAGING"):
                subprocess.run(["docker", "push", staging_tag], check=False)
            push = subprocess.run(["docker", "push", final_tag], check=False, capture_output=True, text=True)
            if push.returncode != 0:
                raise SystemExit(f"docker push to Nexus failed: {push.stderr or push.stdout}")

        digest = ""
        if not skip_push:
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
                    "skipPush": skip_push,
                    "imageDigest": digest,
                    "capabilityProfile": capability,
                    "stagingRepository": arts.get("stagingRepository"),
                    "finalRepository": arts.get("finalRepository"),
                    "imageTag": arts.get("imageTag"),
                    "finalTag": final_tag,
                    "localTar": str(local_tar) if local_tar else None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "dryRun": False,
                    "skipPush": skip_push,
                    "imageDigest": digest,
                    "finalTag": final_tag,
                    "localTar": str(local_tar) if local_tar else None,
                }
            )
        )


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
