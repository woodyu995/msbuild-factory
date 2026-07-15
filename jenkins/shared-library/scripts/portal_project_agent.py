#!/usr/bin/env python3
"""Portal project-build helper for Jenkins pipelines."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _resolve_api_token(args: argparse.Namespace) -> str | None:
    token = (
        getattr(args, "api_token", None)
        or os.environ.get("PORTAL_API_TOKEN")
        or os.environ.get("PORTAL_BEARER_TOKEN")
    )
    return token.strip() if token else None


def _resolve_hmac_secret(args: argparse.Namespace) -> str:
    secret = (
        getattr(args, "hmac_secret", None)
        or os.environ.get("PORTAL_HMAC_SECRET")
        or os.environ.get("PORTAL_CALLBACK_HMAC_SECRET")
    )
    if not secret:
        raise SystemExit("HMAC secret required via --hmac-secret or PORTAL_HMAC_SECRET")
    return secret


def _request(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    hmac_secret: str | None = None,
    api_token: str | None = None,
    accept: str = "application/json",
    as_text: bool = False,
) -> Any:
    data = None
    headers = {"Accept": accept}
    raw = b""
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        data = raw
        headers["Content-Type"] = "application/json"
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    if hmac_secret is not None:
        ts = str(int(time.time()))
        headers["X-Timestamp"] = ts
        headers["X-Signature"] = _sign(hmac_secret, ts, raw)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8")
            if as_text:
                return payload
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {url}: {detail}") from exc


def cmd_resolve(args: argparse.Namespace) -> None:
    url = f"{args.portal_url.rstrip('/')}/api/v1/build-requests/{args.request_id}"
    row = _request("GET", url, api_token=_resolve_api_token(args))
    out = {
        "id": row["id"],
        "status": row["status"],
        "repository": row["repository"],
        "resolvedCommit": row["resolvedCommit"],
        "solutionPath": row["solutionPath"],
        "configuration": row["configuration"],
        "platform": row["platform"],
        "nugetMode": row["nugetMode"],
        "imageDigest": row.get("imageDigest"),
        "matchType": row.get("matchType"),
        "requestedProfileHash": row.get("requestedProfileHash"),
        "matchedProfileHash": row.get("matchedProfileHash"),
        "windowsBase": row.get("windowsBase"),
        "environment": row.get("environment"),
    }
    print(json.dumps(out, ensure_ascii=False))


def cmd_pod_template(args: argparse.Namespace) -> None:
    fmt = args.format or "yaml"
    url = (
        f"{args.portal_url.rstrip('/')}/api/v1/build-requests/"
        f"{args.request_id}/pod-template?format={fmt}"
    )
    token = _resolve_api_token(args)
    if fmt == "json":
        print(json.dumps(_request("GET", url, api_token=token), ensure_ascii=False))
        return
    text = _request("GET", url, accept="application/yaml", as_text=True, api_token=token)
    sys.stdout.write(text)


def cmd_event(args: argparse.Namespace) -> None:
    url = f"{args.portal_url.rstrip('/')}/internal/v1/build-events"
    body: dict[str, Any] = {
        "requestId": args.request_id,
        "eventType": args.event_type,
        "message": args.message,
    }
    if args.build_number:
        try:
            body["jenkinsBuildNumber"] = int(args.build_number)
        except ValueError:
            pass
    print(
        json.dumps(
            _request(
                "POST",
                url,
                body=body,
                hmac_secret=_resolve_hmac_secret(args),
            )
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portal project build agent")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_api_token(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--api-token",
            default="",
            help="Bearer token for PORTAL_REQUIRE_AUTH (or PORTAL_API_TOKEN env)",
        )

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--portal-url", required=True)
    p_resolve.add_argument("--request-id", required=True)
    add_api_token(p_resolve)

    p_pod = sub.add_parser("pod-template")
    p_pod.add_argument("--portal-url", required=True)
    p_pod.add_argument("--request-id", required=True)
    p_pod.add_argument("--format", default="yaml", choices=["yaml", "json"])
    add_api_token(p_pod)

    p_event = sub.add_parser("event")
    p_event.add_argument("--portal-url", required=True)
    p_event.add_argument("--hmac-secret", default="")
    p_event.add_argument("--request-id", required=True)
    p_event.add_argument("--event-type", required=True)
    p_event.add_argument("--message", required=True)
    p_event.add_argument("--build-number", default="")

    args = parser.parse_args(argv)
    if args.command == "resolve":
        cmd_resolve(args)
    elif args.command == "pod-template":
        cmd_pod_template(args)
    else:
        cmd_event(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
