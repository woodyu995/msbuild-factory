#!/usr/bin/env python3
"""Portal project-build helper for Jenkins pipelines."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
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
    row = _request("GET", url)
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
    if fmt == "json":
        print(json.dumps(_request("GET", url), ensure_ascii=False))
        return
    text = _request("GET", url, accept="application/yaml", as_text=True)
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
    print(json.dumps(_request("POST", url, body=body, hmac_secret=args.hmac_secret)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portal project build agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--portal-url", required=True)
    p_resolve.add_argument("--request-id", required=True)

    p_pod = sub.add_parser("pod-template")
    p_pod.add_argument("--portal-url", required=True)
    p_pod.add_argument("--request-id", required=True)
    p_pod.add_argument("--format", default="yaml", choices=["yaml", "json"])

    p_event = sub.add_parser("event")
    p_event.add_argument("--portal-url", required=True)
    p_event.add_argument("--hmac-secret", required=True)
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
